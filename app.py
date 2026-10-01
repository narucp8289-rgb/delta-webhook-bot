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
    "BTC-USDT": {
        "tag": "🟧 ₿ BITCOIN 🟧",
        "buy_hdr": "🟩 BUY SIGNAL: BTC-USDT 🟩",
        "sell_hdr": "🟥 SELL SIGNAL: BTC-USDT 🟥"
    },
    "ETH-USDT": {
        "tag": "🟦 🔷 ETHEREUM 🟦",
        "buy_hdr": "🟩 BUY SIGNAL: ETH-USDT 🟩",
        "sell_hdr": "🟥 SELL SIGNAL: ETH-USDT 🟥"
    },
    "SOL-USDT": {
        "tag": "🟪 🟣 SOLANA 🟪",
        "buy_hdr": "🟪🟩 BUY SIGNAL: SOL-USDT 🟪🟩",
        "sell_hdr": "🟪🟥 SELL SIGNAL: SOL-USDT 🟪🟥"
    },
    "XAUT-USDT": {
        "tag": "🟨 🪙 GOLD / XAUT 🟨",
        "buy_hdr": "🟨🟩 BUY SIGNAL: XAUT-USDT 🟨🟩",
        "sell_hdr": "🟨🟥 SELL SIGNAL: XAUT-USDT 🟨🟥"
    }
}

latest_market_data = {}
last_error = "None"
active_signals = {symbol: None for symbol in SYMBOLS}  
last_processed_candle_ts = {symbol: None for symbol in SYMBOLS}  

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

def get_candles(symbol, bar="15m", limit=100):
    global last_error
    try:
        params = {"instId": symbol, "bar": bar, "limit": limit}
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

def find_major_zones(df):
    major_highs = []
    major_lows = []

    for i in range(len(df) - 5, 5, -1):
        if (df.iloc[i]['high'] > df.iloc[i-1]['high'] and 
            df.iloc[i]['high'] > df.iloc[i-2]['high'] and 
            df.iloc[i]['high'] > df.iloc[i+1]['high'] and 
            df.iloc[i]['high'] > df.iloc[i+2]['high']):
            major_highs.append(float(df.iloc[i]['high']))

        if (df.iloc[i]['low'] < df.iloc[i-1]['low'] and 
            df.iloc[i]['low'] < df.iloc[i-2]['low'] and 
            df.iloc[i]['low'] < df.iloc[i+1]['low'] and 
            df.iloc[i]['low'] < df.iloc[i+2]['low']):
            major_lows.append(float(df.iloc[i]['low']))

    major_high = max(major_highs) if major_highs else float(df.iloc[-35:-3]['high'].max())
    major_low = min(major_lows) if major_lows else float(df.iloc[-35:-3]['low'].min())

    return major_high, major_low

def calculate_indicators(symbol):
    df_15m = get_candles(symbol, bar="15m", limit=100)
    if df_15m is None or len(df_15m) < 40:
        return None

    entry_candle = df_15m.iloc[-2]
    
    candle_ts = str(entry_candle["ts"])
    entry_close = float(entry_candle["close"])
    entry_open = float(entry_candle["open"])
    entry_high = float(entry_candle["high"])
    entry_low = float(entry_candle["low"])

    major_high, major_low = find_major_zones(df_15m)

    is_red = entry_close < entry_open
    is_green = entry_close > entry_open

    # 📌 SELL: Major Resistance સ્વીપ કરીને બની Green કેન્ડલ
    sell_signal = bool((entry_high > major_high) and (entry_close < major_high) and is_green)

    # 📌 BUY: Major Support સ્વીપ કરીને બની Red કેન્ડલ
    buy_signal = bool((entry_low < major_low) and (entry_close > major_low) and is_red)

    return {
        "symbol": symbol,
        "candle_ts": candle_ts,
        "price": round(entry_close, 2),     # Entry Price (Close)
        "high": round(entry_high, 2),       # High (SL for Sell)
        "low": round(entry_low, 2),         # Low (SL for Buy)
        "open": round(entry_open, 2),
        "major_high": round(major_high, 2),
        "major_low": round(major_low, 2),
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
                    current_candle_ts = data["candle_ts"]
                    
                    cfg = SYMBOL_CONFIG.get(symbol, {"tag": symbol, "buy_hdr": f"*BUY: {symbol}*", "sell_hdr": f"*SELL: {symbol}*"})

                    curr_df = get_candles(symbol, bar="1m", limit=2)
                    if curr_df is not None and len(curr_df) > 0:
                        live_high = float(curr_df.iloc[-1]["high"])
                        live_low = float(curr_df.iloc[-1]["low"])
                    else:
                        live_high, live_low = data["high"], data["low"]

                    # 📌 Active Positions SL / TP Tracking
                    if active_signals[symbol] is not None:
                        act = active_signals[symbol]
                        side = act["side"]
                        sl = act["sl"]
                        tp = act["tp"]
                        entry = act["entry"]

                        if side == "BUY":
                            if live_high >= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif live_low <= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                        elif side == "SELL":
                            if live_low <= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif live_high >= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                    # 📌 Signal Generation
                    if active_signals[symbol] is None and last_processed_candle_ts[symbol] != current_candle_ts:

                        # 1. SELL SIGNAL
                        if data["sell_signal"]:
                            entry_price = data["price"]    # Green Candle Close
                            sl = data["high"]              # Green Candle High (SL)
                            tp = data["major_low"]         # Target: Major Support Zone

                            sl_pts = round(abs(sl - entry_price), 2)
                            tp_pts = round(abs(entry_price - tp), 2)

                            if sl > entry_price and entry_price > tp and sl_pts > 0:
                                active_signals[symbol] = {"side": "SELL", "sl": sl, "tp": tp, "entry": entry_price}
                                last_processed_candle_ts[symbol] = current_candle_ts

                                msg = (f"{cfg['tag']}\n"
                                       f"{cfg['sell_hdr']}\n"
                                       f"━━━━━━━━━━━━━━━━━━\n"
                                       f"📌 Entry Price: ${entry_price}\n"
                                       f"🛑 Stop Loss: ${sl} (*{sl_pts} Points*)\n"
                                       f"🎯 Take Profit: ${tp} (*{tp_pts} Points*)\n"
                                       f"📈 Broken High: ${data['major_high']}\n"
                                       f"━━━━━━━━━━━━━━━━━━\n"
                                       f"👉 Delta Exchange: SELL / SHORT")
                                send_telegram(msg)

                        # 2. BUY SIGNAL
                        elif data["buy_signal"]:
                            entry_price = data["price"]    # Red Candle Close
                            sl = data["low"]               # Red Candle Low (SL)
                            tp = data["major_high"]        # Target: Major Resistance Zone

                            sl_pts = round(abs(entry_price - sl), 2)
                            tp_pts = round(abs(tp - entry_price), 2)

                            if entry_price > sl and tp > entry_price and sl_pts > 0:
                                active_signals[symbol] = {"side": "BUY", "sl": sl, "tp": tp, "entry": entry_price}
                                last_processed_candle_ts[symbol] = current_candle_ts

                                msg = (f"{cfg['tag']}\n"
                                       f"{cfg['buy_hdr']}\n"
                                       f"━━━━━━━━━━━━━━━━━━\n"
                                       f"📌 Entry Price: ${entry_price}\n"
                                       f"🛑 Stop Loss: ${sl} (*{sl_pts} Points*)\n"
                                       f"🎯 Take Profit: ${tp} (*{tp_pts} Points*)\n"
                                       f"📉 Broken Low: ${data['major_low']}\n"
                                       f"━━━━━━━━━━━━━━━━━━\n"
                                       f"👉 Delta Exchange: BUY / LONG")
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
        "mode": "Fixed Sweep Candle SL & Points Bot",
        "active_signals": active_signals,
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    send_telegram("⚡ *Fixed Sweep Candle SL & Points Bot Active*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
