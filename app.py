import os
import requests
import pandas as pd
import numpy as np
import threading
import time
from datetime import datetime
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
OKX_URL = "https://www.okx.com/api/v5/market/candles"

# XAUT-USDT ની જગ્યાએ GOLD-USDT નામ રાખ્યું છે
SYMBOLS = ["ETH-USDT", "BTC-USDT", "SOL-USDT", "GOLD-USDT"]

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# OKX API માટે સાચો Symbol Mapping (Gold માટે OKX પર XAU-USDT ચાલશે)
OKX_SYMBOL_MAP = {
    "BTC-USDT": "BTC-USDT",
    "ETH-USDT": "ETH-USDT",
    "SOL-USDT": "SOL-USDT",
    "GOLD-USDT": "XAU-USDT"
}

# 🎨 કલર કોડેડ બોટ મેસેજ ફોર્મેટ
SYMBOL_CONFIG = {
    "BTC-USDT": {
        "tag": "🟧 ₿ [ BITCOIN ] 🟧",
        "buy_hdr": "🟧🟩 *BUY SIGNAL: BTC-USDT* 🟧🟩",
        "sell_hdr": "🟧🟥 *SELL SIGNAL: BTC-USDT* 🟧🟥"
    },
    "ETH-USDT": {
        "tag": "🟦 🔷 [ ETHEREUM ] 🟦",
        "buy_hdr": "🟦🟩 *BUY SIGNAL: ETH-USDT* 🟦🟩",
        "sell_hdr": "🟦🟥 *SELL SIGNAL: ETH-USDT* 🟦🟥"
    },
    "SOL-USDT": {
        "tag": "🟪 🟣 [ SOLANA ] 🟪",
        "buy_hdr": "🟪🟩 *BUY SIGNAL: SOL-USDT* 🟪🟩",
        "sell_hdr": "🟪🟥 *SELL SIGNAL: SOL-USDT* 🟪🟥"
    },
    "GOLD-USDT": {
        "tag": "🟨 🪙 [ GOLD ] 🟨",
        "buy_hdr": "🟨🟩 *BUY SIGNAL: GOLD-USDT* 🟨🟩",
        "sell_hdr": "🟨🟥 *SELL SIGNAL: GOLD-USDT* 🟨🟥"
    }
}

latest_market_data = {}
last_error = "None"
active_signals = {symbol: None for symbol in SYMBOLS}  
last_processed_candle_ts = {symbol: None for symbol in SYMBOLS}  

htf_cache = {}

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_candles(symbol, bar="3m", limit=100):
    global last_error
    try:
        okx_symbol = OKX_SYMBOL_MAP.get(symbol, symbol)
        params = {"instId": okx_symbol, "bar": bar, "limit": limit}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(OKX_URL, params=params, headers=headers, timeout=4)
        res = response.json()
        
        if res.get("code") == "0" and "data" in res:
            raw_data = res["data"]
            if len(raw_data) > 0:
                df = pd.DataFrame(raw_data, columns=[
                    "ts", "open", "high", "low", "close", "volume", 
                    "volCcy", "volCcyQuote", "confirm"
                ])
                for col in ["close", "high", "low", "open", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

                df = df.iloc[::-1].reset_index(drop=True)
                df = df.dropna(subset=["close", "high", "low", "volume"]).reset_index(drop=True)
                return df
    except Exception as e:
        last_error = f"Fetch exception ({symbol} - {bar}): {e}"
    return None

def calculate_rsi(close_series, period=14):
    try:
        if len(close_series) < period + 1:
            return pd.Series([50.0] * len(close_series))

        delta = close_series.diff()
        gain = delta.clip(lower=0.0)
        loss = -1.0 * delta.clip(upper=0.0)

        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

        rs = avg_gain / (avg_loss + 1e-10)
        return 100.0 - (100.0 / (1.0 + rs))
    except Exception as e:
        return pd.Series([50.0] * len(close_series))

def calculate_adx(df, period=14):
    try:
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        df['+dm'] = np.where((df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']), np.maximum(df['high'] - df['high'].shift(1), 0), 0)
        df['-dm'] = np.where((df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)), np.maximum(df['low'].shift(1) - df['low'], 0), 0)

        tr_smooth = df['tr'].ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        plus_di = 100 * (df['+dm'].ewm(alpha=1/period, min_periods=period, adjust=False).mean() / (tr_smooth + 1e-10))
        minus_di = 100 * (df['-dm'].ewm(alpha=1/period, min_periods=period, adjust=False).mean() / (tr_smooth + 1e-10))

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        return adx.fillna(0)
    except Exception as e:
        return pd.Series([0.0] * len(df))

def get_cached_htf_trends(symbol):
    now = time.time()
    if symbol in htf_cache and (now - htf_cache[symbol]['time']) < 300:
        return htf_cache[symbol]['is_double_uptrend'], htf_cache[symbol]['is_double_downtrend'], htf_cache[symbol]['trend_1h'], htf_cache[symbol]['trend_30m']

    df_1h = get_candles(symbol, bar="1H", limit=50)
    df_30m = get_candles(symbol, bar="30m", limit=50)

    if df_1h is None or df_30m is None:
        return False, False, "DOWN", "DOWN"

    df_1h["ema_21_1h"] = df_1h["close"].ewm(span=21, adjust=False).mean()
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()

    close_1h, ema_1h = float(df_1h["close"].iloc[-1]), float(df_1h["ema_21_1h"].iloc[-1])
    close_30m, ema_30m = float(df_30m["close"].iloc[-1]), float(df_30m["ema_21_30m"].iloc[-1])

    is_1h_uptrend = close_1h >= ema_1h
    is_30m_uptrend = close_30m >= ema_30m

    is_double_uptrend = is_1h_uptrend and is_30m_uptrend
    is_double_downtrend = (not is_1h_uptrend) and (not is_30m_uptrend)

    trend_1h = "UP" if is_1h_uptrend else "DOWN"
    trend_30m = "UP" if is_30m_uptrend else "DOWN"

    htf_cache[symbol] = {
        'is_double_uptrend': is_double_uptrend,
        'is_double_downtrend': is_double_downtrend,
        'trend_1h': trend_1h,
        'trend_30m': trend_30m,
        'time': now
    }

    return is_double_uptrend, is_double_downtrend, trend_1h, trend_30m

def calculate_indicators(symbol):
    df_3m = get_candles(symbol, bar="3m", limit=100)
    if df_3m is None or len(df_3m) < 35:
        return None

    is_double_uptrend, is_double_downtrend, trend_1h, trend_30m = get_cached_htf_trends(symbol)

    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["adx"] = calculate_adx(df_3m, 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20, min_periods=1).mean()

    # 📌 કેન્ડલ્સ વ્યાખ્યાયિત કરો
    entry_candle = df_3m.iloc[-2]  # ક્લોઝ થયેલી કેન્ડલ
    prev_candle = df_3m.iloc[-3]   # એના અગાઉની કેન્ડલ

    candle_ts = str(entry_candle["ts"])
    entry_close = float(entry_candle["close"])
    entry_open = float(entry_candle["open"])
    entry_high = float(entry_candle["high"])
    entry_low = float(entry_candle["low"])
    
    prev_close = float(prev_candle["close"])
    
    ema_21_val = float(entry_candle["ema_21_3m"])
    rsi_val = float(entry_candle["rsi"])
    adx_val = float(entry_candle["adx"])

    vol_val = float(entry_candle["volume"])
    vol_avg_val = float(entry_candle["vol_avg"])
    
    has_volume_spike_1_5x = vol_val >= (1.5 * vol_avg_val)
    has_strong_trend = adx_val >= 25.0
    
    candle_range = max(entry_high - entry_low, 1e-5)
    candle_body = abs(entry_close - entry_open)
    is_strong_body = (candle_body / candle_range) >= 0.60

    # 📌 સ્વિંગ શોધવા માટેનો ડેટા (છેલ્લા 25 બાર્સ)
    swing_df = df_3m.iloc[-27:-2].reset_index(drop=True)
    
    swing_high = None
    swing_low = None

    # ૧. PROPER U-TURN & 3 CONSECUTIVE CANDLES CHECK FOR SWING HIGH
    for i in range(len(swing_df) - 4, 2, -1):
        curr_high = swing_df.iloc[i]["high"]
        left_high = swing_df.iloc[i-1]["high"]
        right_high = swing_df.iloc[i+1]["high"]

        is_u_turn_high = (curr_high > left_high) and (curr_high > right_high)

        c1_green = swing_df.iloc[i]["close"] > swing_df.iloc[i]["open"]
        c2_green = swing_df.iloc[i-1]["close"] > swing_df.iloc[i-1]["open"]
        c3_green = swing_df.iloc[i-2]["close"] > swing_df.iloc[i-2]["open"]
        is_3_green = c1_green and c2_green and c3_green

        if is_u_turn_high or is_3_green:
            swing_high = float(curr_high)
            break

    # ૨. PROPER U-TURN & 3 CONSECUTIVE CANDLES CHECK FOR SWING LOW
    for i in range(len(swing_df) - 4, 2, -1):
        curr_low = swing_df.iloc[i]["low"]
        left_low = swing_df.iloc[i-1]["low"]
        right_low = swing_df.iloc[i+1]["low"]

        is_u_turn_low = (curr_low < left_low) and (curr_low < right_low)

        c1_red = swing_df.iloc[i]["close"] < swing_df.iloc[i]["open"]
        c2_red = swing_df.iloc[i-1]["close"] < swing_df.iloc[i-1]["open"]
        c3_red = swing_df.iloc[i-2]["close"] < swing_df.iloc[i-2]["open"]
        is_3_red = c1_red and c2_red and c3_red

        if is_u_turn_low or is_3_red:
            swing_low = float(curr_low)
            break

    if swing_high is None:
        swing_high = float(swing_df["high"].max())
    if swing_low is None:
        swing_low = float(swing_df["low"].min())

    is_fresh_buy_breakout = (prev_close <= swing_high) and (entry_close > swing_high)
    is_fresh_sell_breakout = (prev_close >= swing_low) and (entry_close < swing_low)

    buy_signal = bool(
        is_double_uptrend and 
        is_fresh_buy_breakout and 
        (entry_close > ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val > 52.0)
    )

    sell_signal = bool(
        is_double_downtrend and 
        is_fresh_sell_breakout and 
        (entry_close < ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val < 48.0)
    )

    return {
        "symbol": symbol,
        "candle_ts": candle_ts,
        "price": round(entry_close, 2),
        "high": round(entry_high, 2),
        "low": round(entry_low, 2),
        "trend_1h": trend_1h,
        "trend_30m": trend_30m,
        "ema_21_3m": round(ema_21_val, 2),
        "rsi": round(rsi_val, 2),
        "adx": round(adx_val, 2),
        "swing_low": round(swing_low, 2),
        "swing_high": round(swing_high, 2),
        "buy_signal": buy_signal,
        "sell_signal": sell_signal
    }

def alert_bot_loop():
    global latest_market_data, active_signals, last_processed_candle_ts
    while True:
        for symbol in SYMBOLS:
            try:
                data = calculate_indicators(symbol)
                if data:
                    latest_market_data[symbol] = data
                    price = data["price"]
                    high = data["high"]
                    low = data["low"]
                    current_candle_ts = data["candle_ts"]
                    
                    cfg = SYMBOL_CONFIG.get(symbol, {"tag": symbol, "buy_hdr": f"*BUY: {symbol}*", "sell_hdr": f"*SELL: {symbol}*"})

                    # SL / TP Tracker Logic
                    if active_signals[symbol] is not None:
                        act = active_signals[symbol]
                        side = act["side"]
                        sl = act["sl"]
                        tp = act["tp"]
                        entry = act["entry"]

                        if side == "BUY":
                            if high >= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif low <= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                        elif side == "SELL":
                            if low <= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif high >= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                    # ⚡ FIXED SL / TP DISTANCE SETTINGS
                    if active_signals[symbol] is None and last_processed_candle_ts[symbol] != current_candle_ts:
                        
                        if "BTC" in symbol:
                            sl_dist = 500.0   # Fixed SL $500
                            tp_dist = 1000.0  # Fixed TP $1000
                        elif "ETH" in symbol:
                            sl_dist = 15.0    # Fixed SL $15
                            tp_dist = 30.0    # Fixed TP $30
                        elif "GOLD" in symbol:
                            sl_dist = 5.0     # Fixed SL $5
                            tp_dist = 10.0    # Fixed TP $10
                        else:  # SOL-USDT
                            sl_dist = 1.0     # Fixed SL $1.0
                            tp_dist = 2.0     # Fixed TP $2.0

                        if data["buy_signal"]:
                            sl = round(price - sl_dist, 2)
                            tp = round(price + tp_dist, 2)
                            active_signals[symbol] = {"side": "BUY", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts
                            
                            msg = (f"{cfg['tag']}\n"
                                   f"{cfg['buy_hdr']}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"📌 *Entry Price:* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📉 *Broken High:* ${data['swing_high']}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"👉 Delta Exchange: **BUY / LONG**")
                            send_telegram(msg)

                        elif data["sell_signal"]:
                            sl = round(price + sl_dist, 2)
                            tp = round(price - tp_dist, 2)
                            active_signals[symbol] = {"side": "SELL", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts

                            msg = (f"{cfg['tag']}\n"
                                   f"{cfg['sell_hdr']}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"📌 *Entry Price:* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📈 *Broken Low:* ${data['swing_low']}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"👉 Delta Exchange: **SELL / SHORT**")
                            send_telegram(msg)

            except Exception as e:
                print(f"Loop Error ({symbol}): {e}")
            time.sleep(0.1)
        time.sleep(0.3)

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    global latest_market_data, active_signals
    return jsonify({
        "status": "running",
        "mode": "Proper Swing & Fresh Breakout Bot",
        "active_signals": active_signals,
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    send_telegram("⚡ *Proper Swing & Fresh Breakout Bot Active*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
