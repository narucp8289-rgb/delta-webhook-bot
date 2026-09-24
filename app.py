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
    "BTC-USDT": {
        "screener": "crypto", 
        "exchange": "BINANCE", 
        "symbol": "BTCUSDT", 
        "tag": "🟧 ₿ [ BITCOIN - TRADINGVIEW ] 🟧",
        "buy_hdr": "🟧🟩 *BUY SIGNAL: BTC-USDT* 🟧🟩",
        "sell_hdr": "🟧🟥 *SELL SIGNAL: BTC-USDT* 🟧🟥"
    },
    "ETH-USDT": {
        "screener": "crypto", 
        "exchange": "BINANCE", 
        "symbol": "ETHUSDT", 
        "tag": "🟦 🔷 [ ETHEREUM - TRADINGVIEW ] 🟦",
        "buy_hdr": "🟦🟩 *BUY SIGNAL: ETH-USDT* 🟦🟩",
        "sell_hdr": "🟦🟥 *SELL SIGNAL: ETH-USDT* 🟦🟥"
    },
    "SOL-USDT": {
        "screener": "crypto", 
        "exchange": "BINANCE", 
        "symbol": "SOLUSDT", 
        "tag": "🟪 🟣 [ SOLANA - TRADINGVIEW ] 🟪",
        "buy_hdr": "🟪🟩 *BUY SIGNAL: SOL-USDT* 🟪🟩",
        "sell_hdr": "🟪🟥 *SELL SIGNAL: SOL-USDT* 🟪🟥"
    },
    "XAUT-USDT": {
        "screener": "crypto", 
        "exchange": "OKX", 
        "symbol": "XAUTUSDT", 
        "tag": "🟨 🪙 [ GOLD / XAUT - TRADINGVIEW ] 🟨",
        "buy_hdr": "🟨🟩 *BUY SIGNAL: XAUT-USDT* 🟨🟩",
        "sell_hdr": "🟨🟥 *SELL SIGNAL: XAUT-USDT* 🟨🟥"
    }
}

latest_market_data = {}
active_signals = {symbol: None for symbol in SYMBOLS}  
last_signal_time = {symbol: None for symbol in SYMBOLS}  

def get_ist_time():
    return datetime.now(IST).strftime('%I:%M:%S %p')

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

# TradingView માંથી ઈન્ડીકેટર ડેટા લાવવા માટે
def get_tv_indicators(symbol_key):
    try:
        cfg = SYMBOL_CONFIG[symbol_key]
        handler = TA_Handler(
            symbol=cfg["symbol"],
            exchange=cfg["exchange"],
            screener=cfg["screener"],
            interval=Interval.INTERVAL_1_MINUTE,
            timeout=10
        )
        
        # Custom User-Agent ઉમેરીને રિક્વેસ્ટ મોકલવી જેથી TradingView બ્લોક ન કરે
        handler.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        analysis = handler.get_analysis()
        return analysis.indicators, analysis.summary
    except Exception as e:
        print(f"Error fetching TradingView data for {symbol_key}: {e}")
        return None, None

def alert_bot_loop():
    global latest_market_data, active_signals, last_signal_time
    while True:
        for symbol in SYMBOLS:
            try:
                ind, summary = get_tv_indicators(symbol)
                
                if ind and summary:
                    price = float(ind.get("close", 0))
                    high = float(ind.get("high", price))
                    low = float(ind.get("low", price))
                    rsi = float(ind.get("RSI", 50))
                    ema21 = float(ind.get("EMA21", price))
                    rec = summary.get("RECOMMENDATION", "NEUTRAL")

                    cfg = SYMBOL_CONFIG.get(symbol)

                    latest_market_data[symbol] = {
                        "price": price,
                        "rsi": round(rsi, 2),
                        "ema21": round(ema21, 2),
                        "recommendation": rec,
                        "source": "TradingView Official",
                        "time": get_ist_time()
                    }

                    # Stop Loss / Take Profit ટ્રેકર
                    if active_signals[symbol] is not None:
                        act = active_signals[symbol]
                        side, sl, tp, entry = act["side"], act["sl"], act["tp"], act["entry"]

                        if side == "BUY":
                            if high >= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n⏰ *Time:* {get_ist_time()}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif low <= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n⏰ *Time:* {get_ist_time()}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                        elif side == "SELL":
                            if low <= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT!*\n{cfg['tag']}\n⏰ *Time:* {get_ist_time()}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif high >= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT!*\n{cfg['tag']}\n⏰ *Time:* {get_ist_time()}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                    # TradingView Recommendation મુજબ સિગ્નલ
                    current_time_str = datetime.now(IST).strftime('%Y-%m-%d %H:%M')
                    if active_signals[symbol] is None and last_signal_time[symbol] != current_time_str:
                        if "BTC" in symbol:
                            sl_dist, tp_dist = 500.0, 1000.0
                        elif "ETH" in symbol:
                            sl_dist, tp_dist = 15.0, 30.0
                        elif "XAUT" in symbol:
                            sl_dist, tp_dist = 5.0, 10.0
                        else:
                            sl_dist, tp_dist = 1.0, 2.0

                        if rec == "STRONG_BUY" and price > ema21 and rsi > 52:
                            sl = round(price - sl_dist, 2)
                            tp = round(price + tp_dist, 2)
                            active_signals[symbol] = {"side": "BUY", "sl": sl, "tp": tp, "entry": price}
                            last_signal_time[symbol] = current_time_str

                            msg = (f"{cfg['tag']}\n"
                                   f"{cfg['buy_hdr']}\n"
                                   f"⏰ *Time (IST):* {get_ist_time()}\n"
                                   f"📊 *Data Source:* TradingView Technicals\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"📌 *Entry Price:* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📈 *RSI:* {round(rsi, 2)}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"👉 Delta Exchange: **BUY / LONG**")
                            send_telegram(msg)

                        elif rec == "STRONG_SELL" and price < ema21 and rsi < 48:
                            sl = round(price + sl_dist, 2)
                            tp = round(price - tp_dist, 2)
                            active_signals[symbol] = {"side": "SELL", "sl": sl, "tp": tp, "entry": price}
                            last_signal_time[symbol] = current_time_str

                            msg = (f"{cfg['tag']}\n"
                                   f"{cfg['sell_hdr']}\n"
                                   f"⏰ *Time (IST):* {get_ist_time()}\n"
                                   f"📊 *Data Source:* TradingView Technicals\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"📌 *Entry Price:* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📉 *RSI:* {round(rsi, 2)}\n"
                                   f"━━━━━━━━━━━━━━━━━━\n"
                                   f"👉 Delta Exchange: **SELL / SHORT**")
                            send_telegram(msg)

            except Exception as e:
                print(f"Loop error ({symbol}): {e}")
            
            # દરેક પેર વચ્ચે ૫ સેકન્ડનો વિરામ
            time.sleep(5)
        
        # આખી સાયકલ પૂરી થયા પછી ૨૦ સેકન્ડનો વિરામ (Rate Limit થી બચવા માટે)
        time.sleep(20)

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "data_source": "TradingView Live Data",
        "time_ist": get_ist_time(),
        "active_signals": active_signals,
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
