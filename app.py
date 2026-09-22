import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time

# --- TELEGRAM CONFIG ---
BOT_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
CHAT_ID = "5305261922"

# --- EXCHANGE CONFIG (OKX / Delta Data) ---
exchange = ccxt.okx({'enableRateLimit': True})

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XAUT/USDT"]
TIMEFRAME = "3m"

SYMBOL_CONFIG = {
    "BTC/USDT": {"sl_dist": 400.0, "tp_dist": 1200.0},
    "ETH/USDT": {"sl_dist": 12.0, "tp_dist": 36.0},
    "SOL/USDT": {"sl_dist": 0.8, "tp_dist": 2.4},
    "XAUT/USDT": {"sl_dist": 4.0, "tp_dist": 12.0}
}

last_processed_ts = {symbol: None for symbol in SYMBOLS}

def send_telegram_msg(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def fetch_data(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=100)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # EMA 21
        df['ema21'] = ta.ema(df['close'], length=21)
        
        # ADX & ATR
        adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
        df['adx'] = adx_df['ADX_14']
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        # Bollinger Bands (Sideways Filter)
        bb = ta.bbands(df['close'], length=20, std=2)
        df['bb_width'] = (bb['BBU_20_2.0'] - bb['BBL_20_2.0']) / bb['BBM_20_2.0']
        
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol}): {e}")
        return None

def check_signals():
    global last_processed_ts
    
    for symbol in SYMBOLS:
        df = fetch_data(symbol)
        if df is None or len(df) < 40:
            continue
            
        c_candle = df.iloc[-2]      # છેલ્લે કમ્પ્લીટ થયેલી કેન્ડલ (Last Closed)
        prev_candle = df.iloc[-3]   # એની આગળની કેન્ડલ
        live_candle = df.iloc[-1]   # અત્યારે લાઈવ બનતી કેન્ડલ
        
        candle_ts = c_candle['timestamp']
        if last_processed_ts[symbol] == candle_ts:
            continue  # આ કેન્ડલ પર સિગ્નલ પ્રોસેસ થઈ ગયું છે

        # ૧. સાઇડવેઝ / ચોપી માર્કેટ ફિલ્ટર
        is_not_choppy = (c_candle['adx'] >= 20.0) and (c_candle['bb_width'] >= 0.0015)
        if not is_not_choppy:
            continue

        # ૨. સ્વિંગ હાઇ/લો અને સપ્લાય/ડિમાન્ડ ઝોન (છેલ્લા 30 બાર્સ)
        swing_df = df.iloc[-33:-3]
        swing_high = float(swing_df['high'].max()) # Supply Zone
        swing_low = float(swing_df['low'].min())   # Demand Zone

        live_price = float(live_candle['close'])

        # ઝોન કન્ફર્મેશન બફર (0.5%)
        near_demand_zone = c_candle['low'] <= (swing_low * 1.005)
        near_supply_zone = c_candle['high'] >= (swing_high * 0.995)

        cfg = SYMBOL_CONFIG.get(symbol, {"sl_dist": 10.0, "tp_dist": 30.0})

        # --- BUY SIGNAL LOGIC (Demand Zone + Red High Breakout + EMA 21) ---
        if near_demand_zone and (c_candle['close'] > c_candle['ema21']) and (prev_candle['close'] < prev_candle['open']):
            if c_candle['close'] > prev_candle['high']: # Red High Cross
                entry_price = float(c_candle['close'])
                sl = entry_price - cfg["sl_dist"]
                tp = entry_price + cfg["tp_dist"]

                # Price Mismatch Filter (લાઈવ ભાવથી 0.2% થી વધુ ગેપ ન હોવો જોઈએ)
                if abs(live_price - entry_price) / entry_price < 0.002:
                    msg = (
                        f"🚀 <b>STRONG CONFIRMATION BUY SIGNAL</b>\n"
                        f"<b>Coin:</b> {symbol}\n"
                        f"-------------------------------\n"
                        f"🎯 <b>Zone:</b> Demand Zone (${swing_low:.2f})\n"
                        f"📌 <b>Entry Price (Red High Break):</b> ${entry_price:.2f}\n"
                        f"🛑 <b>SL:</b> ${sl:.2f}\n"
                        f"🎯 <b>TP:</b> ${tp:.2f}\n"
                        f"📊 <b>ATR:</b> {c_candle['atr']:.2f} | <b>ADX:</b> {c_candle['adx']:.2f}"
                    )
                    send_telegram_msg(msg)
                    last_processed_ts[symbol] = candle_ts

        # --- SELL SIGNAL LOGIC (Supply Zone + Green Low Breakdown + EMA 21) ---
        elif near_supply_zone and (c_candle['close'] < c_candle['ema21']) and (prev_candle['close'] > prev_candle['open']):
            if c_candle['close'] < prev_candle['low']: # Green Low Breakdown
                entry_price = float(c_candle['close'])
                sl = entry_price + cfg["sl_dist"]
                tp = entry_price - cfg["tp_dist"]

                # Price Mismatch Filter
                if abs(live_price - entry_price) / entry_price < 0.002:
                    msg = (
                        f"🔻 <b>STRONG CONFIRMATION SELL SIGNAL</b>\n"
                        f"<b>Coin:</b> {symbol}\n"
                        f"-------------------------------\n"
                        f"🎯 <b>Zone:</b> Supply Zone (${swing_high:.2f})\n"
                        f"📌 <b>Entry Price (Green Low Break):</b> ${entry_price:.2f}\n"
                        f"🛑 <b>SL:</b> ${sl:.2f}\n"
                        f"🎯 <b>TP:</b> ${tp:.2f}\n"
                        f"📊 <b>ATR:</b> {c_candle['atr']:.2f} | <b>ADX:</b> {c_candle['adx']:.2f}"
                    )
                    send_telegram_msg(msg)
                    last_processed_ts[symbol] = candle_ts

# --- MAIN LOOP ---
if __name__ == "__main__":
    send_telegram_msg("⚡ <b>High-Confirmation SMC Bot Activated Successfully!</b>")
    while True:
        try:
            check_signals()
            time.sleep(10)  # દર ૧૦ સેકન્ડે નવી કેન્ડલ ક્લોઝિંગ માટે ચેક કરશે
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(5)
