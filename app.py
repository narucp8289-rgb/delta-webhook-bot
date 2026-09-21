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
SYMBOLS = ["ETH-USDT", "BTC-USDT", "SOL-USDT", "XAUT-USDT"]

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

SYMBOL_CONFIG = {
    "BTC-USDT": {"tag": "🟧 ₿ [ BITCOIN - HIGH CONFIRMATION ] 🟧", "sl_dist": 400.0, "tp_dist": 1200.0, "min_atr": 30.0},
    "ETH-USDT": {"tag": "🟦 🔷 [ ETHEREUM - HIGH CONFIRMATION ] 🟦", "sl_dist": 12.0, "tp_dist": 36.0, "min_atr": 1.5},
    "SOL-USDT": {"tag": "🟪 🟣 [ SOLANA - HIGH CONFIRMATION ] 🟪", "sl_dist": 0.8, "tp_dist": 2.4, "min_atr": 0.15},
    "XAUT-USDT": {"tag": "🟨 🪙 [ GOLD - HIGH CONFIRMATION ] 🟨", "sl_dist": 4.0, "tp_dist": 12.0, "min_atr": 0.5}
}

latest_market_data = {}
active_signals = {symbol: None for symbol in SYMBOLS}  
last_processed_candle_ts = {symbol: None for symbol in SYMBOLS}  

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_candles(symbol, bar="3m", limit=100):
    try:
        params = {"instId": symbol, "bar": bar, "limit": limit}
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(OKX_URL, params=params, headers=headers, timeout=4)
        res = response.json()
        
        if res.get("code") == "0" and "data" in res:
            raw_data = res["data"]
            if len(raw_data) > 0:
                df = pd.DataFrame(raw_data, columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"])
                for col in ["close", "high", "low", "open", "volume"]:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df = df.iloc[::-1].reset_index(drop=True)
                return df
    except Exception as e:
        print(f"Fetch Error: {e}")
    return None

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

def calculate_advanced_indicators(symbol):
    df_3m = get_candles(symbol, bar="3m", limit=80)
    if df_3m is None or len(df_3m) < 40:
        return None

    cfg = SYMBOL_CONFIG.get(symbol, {"min_atr": 0.5})

    # ૧. EMA & ADX
    df_3m["ema_21"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["adx"] = calculate_adx(df_3m, 14)

    # ૨. ATR (Volatility Filter)
    df_3m['tr'] = np.maximum(
        df_3m['high'] - df_3m['low'],
        np.maximum(abs(df_3m['high'] - df_3m['close'].shift(1)), abs(df_3m['low'] - df_3m['close'].shift(1)))
    )
    df_3m["atr"] = df_3m['tr'].rolling(14).mean()

    # ૩. Bollinger Bands Width
    df_3m["sma_20"] = df_3m["close"].rolling(20).mean()
    df_3m["std_20"] = df_3m["close"].rolling(20).std()
    df_3m["bb_upper"] = df_3m["sma_20"] + (df_3m["std_20"] * 2)
    df_3m["bb_lower"] = df_3m["sma_20"] - (df_3m["std_20"] * 2)
    df_3m["bb_width"] = (df_3m["bb_upper"] - df_3m["bb_lower"]) / df_3m["sma_20"]

    # ૪. Volume Moving Average
    df_3m["vol_ma"] = df_3m["volume"].rolling(20).mean()

    entry_candle = df_3m.iloc[-2]  # ક્લોઝ થયેલી તાજેતરની કેન્ડલ
    prev_candle = df_3m.iloc[-3]   # અગાઉની કેન્ડલ
    
    candle_ts = str(entry_candle["ts"])
    
    close_p = float(entry_candle["close"])
    open_p = float(entry_candle["open"])
    high_p = float(entry_candle["high"])
    low_p = float(entry_candle["low"])
    vol_p = float(entry_candle["volume"])
    
    prev_close = float(prev_candle["close"])
    prev_open = float(prev_candle["open"])
    prev_high = float(prev_candle["high"])
    prev_low = float(prev_candle["low"])

    adx_curr = float(entry_candle["adx"])
    atr_curr = float(entry_candle["atr"])
    ema_curr = float(entry_candle["ema_21"])
    bb_width_curr = float(entry_candle["bb_width"])
    vol_ma_curr = float(entry_candle["vol_ma"])

    # 🛑 ULTRA SIDEWAYS PROTECTION FILTER:
    # 1. ADX > 20
    # 2. BB Width >= 0.0015
    # 3. ATR > Minimum ATR limit
    is_not_sideways = (adx_curr >= 20.0) and (bb_width_curr >= 0.0015) and (atr_curr >= cfg["min_atr"])

    # 💥 VOLUME CONFIRMATION (અગાઉના ૨૦ બાર્સ કરતાં વધારે વોલ્યુમ હોવું જોઈએ)
    has_volume_support = vol_p >= (vol_ma_curr * 1.05)

    # Supply / Demand Zone
    swing_df = df_3m.iloc[-30:-3]
    swing_high = float(swing_df["high"].max())
    swing_low = float(swing_df["low"].min())

    supply_zone_low = float(swing_df.loc[swing_df["high"] == swing_high, ["open", "close"]].values.max()) if not swing_df.empty else swing_high * 0.998
    demand_zone_high = float(swing_df.loc[swing_df["low"] == swing_low, ["open", "close"]].values.min()) if not swing_df.empty else swing_low * 1.002

    # 🎯 BUY SETUP: Demand Zone + Red Candle High Cross + Volume
    touched_demand = (low_p <= demand_zone_high) or (prev_low <= demand_zone_high)
    prev_was_red = prev_close < prev_open
    crossed_red_high = close_p > prev_high

    buy_signal = bool(is_not_sideways and touched_demand and prev_was_red and crossed_red_high and has_volume_support and (close_p > ema_curr))

    # 🎯 SELL SETUP: Supply Zone + Green Candle Low Break + Volume
    touched_supply = (high_p >= supply_zone_low) or (prev_high >= supply_zone_low)
    prev_was_green = prev_close > prev_open
    crossed_green_low = close_p < prev_low

    sell_signal = bool(is_not_sideways and touched_supply and prev_was_green and crossed_green_low and has_volume_support and (close_p < ema_curr))

    return {
        "symbol": symbol,
        "candle_ts": candle_ts,
        "price": round(close_p, 2),
        "high": round(high_p, 2),
        "low": round(low_p, 2),
        "adx": round(adx_curr, 2),
        "atr": round(atr_curr, 2),
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "buy_signal": buy_signal,
        "sell_signal": sell_signal
    }

def alert_bot_loop():
    global latest_market_data, active_signals, last_processed_candle_ts
    while True:
        for symbol in SYMBOLS:
            try:
                data = calculate_advanced_indicators(symbol)
                if data:
                    latest_market_data[symbol] = data
                    price = data["price"]
                    high = data["high"]
                    low = data["low"]
                    current_candle_ts = data["candle_ts"]
                    cfg = SYMBOL_CONFIG.get(symbol, {"tag": symbol, "sl_dist": 10.0, "tp_dist": 30.0})

                    # SL/TP Monitoring
                    if active_signals[symbol] is not None:
                        act = active_signals[symbol]
                        if act["side"] == "BUY":
                            if high >= act["tp"]:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n📌 Entry: ${act['entry']}\n🎯 TP: ${act['tp']}")
                                active_signals[symbol] = None
                            elif low <= act["sl"]:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n📌 Entry: ${act['entry']}\n🛑 SL: ${act['sl']}")
                                active_signals[symbol] = None
                        elif act["side"] == "SELL":
                            if low <= act["tp"]:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n📌 Entry: ${act['entry']}\n🎯 TP: ${act['tp']}")
                                active_signals[symbol] = None
                            elif high >= act["sl"]:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n📌 Entry: ${act['entry']}\n🛑 SL: ${act['sl']}")
                                active_signals[symbol] = None

                    # New Signal Execution
                    if active_signals[symbol] is None and last_processed_candle_ts[symbol] != current_candle_ts:
                        sl_dist = cfg["sl_dist"]
                        tp_dist = cfg["tp_dist"]

                        if data["buy_signal"]:
                            sl = round(price - sl_dist, 2)
                            tp = round(price + tp_dist, 2)
                            active_signals[symbol] = {"side": "BUY", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts
                            
                            send_telegram(f"🚀 *STRONG CONFIRMATION BUY SIGNAL*\n{cfg['tag']}\n━━━━━━━━━━━━━━━━━━\n📌 *Entry (Red High Breakout):* ${price}\n🛑 *SL:* ${sl}\n🎯 *TP:* ${tp}\n📊 *ATR:* {data['atr']} | *ADX:* {data['adx']}")

                        elif data["sell_signal"]:
                            sl = round(price + sl_dist, 2)
                            tp = round(price - tp_dist, 2)
                            active_signals[symbol] = {"side": "SELL", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts

                            send_telegram(f"🔻 *STRONG CONFIRMATION SELL SIGNAL*\n{cfg['tag']}\n━━━━━━━━━━━━━━━━━━\n📌 *Entry (Green Low Breakdown):* ${price}\n🛑 *SL:* ${sl}\n🎯 *TP:* ${tp}\n📊 *ATR:* {data['atr']} | *ADX:* {data['adx']}")

            except Exception as e:
                print(f"Error ({symbol}): {e}")
            time.sleep(0.1)
        time.sleep(0.3)

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    return jsonify({"status": "running", "mode": "Anti-Sideways + Volume Boosted SMC Bot", "market_data": latest_market_data})

if __name__ == "__main__":
    send_telegram("⚡ *High-Confirmation SMC Bot Active*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
