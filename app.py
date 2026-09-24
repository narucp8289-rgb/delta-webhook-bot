import os
import requests
import pandas as pd
import numpy as np
import threading
import time
from datetime import datetime
import pytz
from flask import Flask, jsonify
from tradingview_ta import TA_Handler, Interval

app = Flask(__name__)

SYMBOLS = ["ETH-USDT", "BTC-USDT", "SOL-USDT", "XAUT-USDT"]

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

IST = pytz.timezone('Asia/Kolkata')

SYMBOL_CONFIG = {
    "BTC-USDT": {"screener": "crypto", "exchange": "BINANCE", "symbol": "BTCUSDT", "tag": "🟧 ₿ [ BITCOIN - TRADINGVIEW ] 🟧"},
    "ETH-USDT": {"screener": "crypto", "exchange": "BINANCE", "symbol": "ETHUSDT", "tag": "🟦 🔷 [ ETHEREUM - TRADINGVIEW ] 🟦"},
    "SOL-USDT": {"screener": "crypto", "exchange": "BINANCE", "symbol": "SOLUSDT", "tag": "🟪 🟣 [ SOLANA - TRADINGVIEW ] 🟪"},
    "XAUT-USDT": {"screener": "crypto", "exchange": "OKX", "symbol": "XAUTUSDT", "tag": "🟨 🪙 [ GOLD / XAUT - TRADINGVIEW ] 🟨"}
}

latest_market_data = {}
active_signals = {symbol: None for symbol in SYMBOLS}  

def get_ist_time():
    return datetime.now(IST).strftime('%I:%M:%S %p')

def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Error: {e}")

def get_tv_analysis(symbol_key):
    try:
        cfg = SYMBOL_CONFIG[symbol_key]
        handler = TA_Handler(
            symbol=cfg["symbol"],
            exchange=cfg["exchange"],
            screener=cfg["screener"],
            interval=Interval.INTERVAL_3_MINUTES
        )
        analysis = handler.get_analysis()
        return analysis.indicators
    except Exception as e:
        print(f"Error fetching TV data for {symbol_key}: {e}")
        return None

def alert_bot_loop():
    global latest_market_data
    while True:
        for symbol in SYMBOLS:
            try:
                ind = get_tv_analysis(symbol)
                if ind:
                    price = ind.get("close")
                    rsi = ind.get("RSI")
                    ema21 = ind.get("EMA21")
                    
                    latest_market_data[symbol] = {
                        "price": price,
                        "rsi": rsi,
                        "ema21": ema21,
                        "time": get_ist_time()
                    }
            except Exception as e:
                print(f"Loop error: {e}")
            time.sleep(1)
        time.sleep(2)

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "data_source": "TradingView Official Indicators",
        "time_ist": get_ist_time(),
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
