import os
import time
import requests
import numpy as np
import pandas as pd
import pandas_ta as ta
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
BASE_URL = "https://api.testnet.delta.exchange"  # Correct Testnet Domain
SYMBOL = "ETHUSD"

# API Credentials from Environment Variables or hardcoded
API_KEY = os.environ.get("DELTA_API_KEY", "YOUR_API_KEY")
API_SECRET = os.environ.get("DELTA_API_SECRET", "YOUR_API_SECRET")

# Telegram Configuration
TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# Risk Management ($15 SL / $30 TP)
SL_AMOUNT = 15.0
TP_AMOUNT = 30.0

# ================= HELPER FUNCTIONS =================
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

def get_candles(resolution="1m", limit=100):
    try:
        url = f"{BASE_URL}/v2/history/candles?resolution={resolution}&symbol={SYMBOL}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        if res.get("success"):
            df = pd.DataFrame(res["result"])
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["open"] = df["open"].astype(float)
            return df
    except Exception as e:
        print(f"Candle fetch error: {e}")
    return None

def calculate_indicators():
    # 4H EMA & Indicators
    df_4h = get_candles(resolution="4h", limit=100)
    df_1m = get_candles(resolution="1m", limit=100)
    
    if df_4h is None or df_1m is None:
        return None

    # Calculate 4H EMA (51)
    df_4h["ema_51"] = ta.ema(df_4h["close"], length=51)
    
    # Calculate 1M Indicators
    df_1m["rsi"] = ta.rsi(df_1m["close"], length=14)
    adx_df = ta.adx(df_1m["high"], df_1m["low"], df_1m["close"], length=14)
    df_1m["adx"] = adx_df["ADX_14"]

    # Calculate Fib 0.5 Retracement
    high_val = df_1m["high"].max()
    low_val = df_1m["low"].min()
    fib_05 = low_val + (high_val - low_val) * 0.5

    latest_1m = df_1m.iloc[-1]
    latest_4h = df_4h.iloc[-1]

    return {
        "price": latest_1m["close"],
        "ema_51": latest_4h["ema_51"],
        "rsi": latest_1m["rsi"],
        "adx": latest_1m["adx"],
        "fib_05": fib_05
    }

# ================= TRADING LOGIC =================
def execute_trade(side, price):
    sl = price - SL_AMOUNT if side == "BUY" else price + SL_AMOUNT
    tp = price + TP_AMOUNT if side == "BUY" else price - TP_AMOUNT
    
    emoji = "🚀" if side == "BUY" else "🔻"
    msg = f"{emoji} *{side} ORDER PLACED!*\n\n*Symbol:* {SYMBOL}\n*Entry Price:* ${price}\n*Stop Loss:* ${sl}\n*Take Profit:* ${tp}"
    
    print(f"Executing {side} Trade at {price}")
    send_telegram(msg)
    # Delta Order Placement API Integration logic connects here

@app.route('/')
def home():
    data = calculate_indicators()
    if data:
        price = data["price"]
        fib_05 = data["fib_05"]
        rsi = data["rsi"]
        adx = data["adx"]
        ema = data["ema_51"]

        # Fib 0.5 Strategy Conditions
        if price >= fib_05 and price > ema and (48 <= rsi <= 52) and adx > 20:
            execute_trade("BUY", price)
        elif price <= fib_05 and price < ema and (48 <= rsi <= 52) and adx > 20:
            execute_trade("SELL", price)

        return jsonify({
            "status": "running",
            "price": price,
            "fib_05": fib_05,
            "rsi": rsi,
            "adx": adx,
            "ema_51": ema
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("🤖 *Bot Started Successfully!* Waiting for trade signals...")
    app.run(host="0.0.0.0", port=10000)
