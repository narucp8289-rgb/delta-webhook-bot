import os
import requests
import pandas as pd
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
BASE_URL = "https://api.testnet.delta.exchange"
SYMBOL = "ETHUSD"

# Telegram Configuration
TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# Risk Management ($20 SL / $40 TP)
SL_AMOUNT = 20.0
TP_AMOUNT = 40.0

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

def get_candles(resolution="15m", limit=100):
    try:
        url = f"{BASE_URL}/v2/history/candles?resolution={resolution}&symbol={SYMBOL}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        if res.get("success"):
            df = pd.DataFrame(res["result"])
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["open"] = df["open"].astype(float)
            df["volume"] = df["volume"].astype(float)
            return df
    except Exception as e:
        print(f"Candle fetch error ({resolution}): {e}")
    return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_indicators():
    df_4h = get_candles(resolution="4h", limit=100)
    df_15m = get_candles(resolution="15m", limit=100)
    
    if df_4h is None or df_15m is None or len(df_4h) < 50 or len(df_15m) < 50:
        return None

    # 1. 4H Trend Determination (4H 200 EMA)
    df_4h["ema_200_4h"] = df_4h["close"].ewm(span=200, adjust=False).mean()
    latest_4h_close = float(df_4h["close"].iloc[-1])
    latest_4h_ema = float(df_4h["ema_200_4h"].iloc[-1])
    is_4h_uptrend = latest_4h_close > latest_4h_ema

    # 2. 15M Indicators
    df_15m["rsi"] = calculate_rsi(df_15m["close"], 14)
    df_15m["vol_avg"] = df_15m["volume"].rolling(window=20).mean()

    # Support / Resistance Levels (Last 20 candles high/low)
    df_15m["recent_high"] = df_15m["high"].iloc[-20:-1].max()
    df_15m["recent_low"] = df_15m["low"].iloc[-20:-1].min()

    latest_15m = df_15m.iloc[-1]

    # Volume Spike Check (1.5x of 20-period Average)
    has_volume_spike = float(latest_15m["volume"]) >= (1.5 * float(latest_15m["vol_avg"]))

    return {
        "price": float(latest_15m["close"]),
        "rsi": float(latest_15m["rsi"]),
        "volume_spike": has_volume_spike,
        "recent_high": float(latest_15m["recent_high"]),
        "recent_low": float(latest_15m["recent_low"]),
        "is_4h_uptrend": is_4h_uptrend
    }

def execute_trade(side, price):
    sl = price - SL_AMOUNT if side == "BUY" else price + SL_AMOUNT
    tp = price + TP_AMOUNT if side == "BUY" else price - TP_AMOUNT
    
    emoji = "🚀" if side == "BUY" else "🔻"
    msg = (f"{emoji} *HIGH CONFIRMATION {side} TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (-${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (+${TP_AMOUNT})\n"
           f"*Strategy:* 4H Trend + 15M Breakout/Breakdown + Vol Spike")
    
    print(f"Executing {side} Trade at {price}")
    send_telegram(msg)

@app.route('/')
def home():
    data = calculate_indicators()
    if data:
        price = data["price"]
        rsi = data["rsi"]
        vol_spike = data["volume_spike"]
        rec_high = data["recent_high"]
        rec_low = data["recent_low"]
        is_4h_uptrend = data["is_4h_uptrend"]

        # BUY / LONG Signal Conditions:
        # 1. 4H Trend is UP (Price > 4H 200 EMA)
        # 2. 15M Candle Close Breakout above Recent High
        # 3. Volume Spike (1.5x)
        # 4. RSI > 50
        if is_4h_uptrend and price > rec_high and vol_spike and rsi > 50:
            execute_trade("BUY", price)

        # SELL / SHORT Signal Conditions:
        # 1. 4H Trend is DOWN (Price < 4H 200 EMA)
        # 2. 15M Candle Close Breakdown below Recent Low
        # 3. Volume Spike (1.5x)
        # 4. RSI < 50
        elif (not is_4h_uptrend) and price < rec_low and vol_spike and rsi < 50:
            execute_trade("SELL", price)

        return jsonify({
            "status": "running",
            "strategy": "4H Trend + 15M Breakout/Breakdown",
            "price": price,
            "rsi": rsi,
            "vol_spike": vol_spike,
            "is_4h_uptrend": is_4h_uptrend
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("🤖 *Bot Updated with 4H Trend & 15M Breakdown Strategy!* Listening for signals...")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
