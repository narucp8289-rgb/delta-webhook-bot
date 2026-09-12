import os
import requests
import pandas as pd
import threading
import time
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
BASE_URL = "https://api.testnet.delta.exchange"
SYMBOL = "ETHUSD"

# Telegram Configuration
TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# Risk Management ($15 SL / $30 TP)
SL_AMOUNT = 15.0
TP_AMOUNT = 30.0

# Active Trade Tracker State
active_position = None

# ================= KEEP-ALIVE (AUTOMATIC ANTI-SLEEP) =================
def keep_alive():
    """Render પર સર્વર સ્લીપ ન થાય તે માટે દર 5 મિનિટે ઓટોમેટિક પિંગ કરશે"""
    time.sleep(10)
    while True:
        try:
            render_url = os.environ.get("RENDER_EXTERNAL_URL")
            if render_url:
                requests.get(render_url, timeout=10)
                print("⚡ Keep-Alive Ping Sent Successfully!")
            else:
                requests.get("http://127.0.0.1:10000", timeout=5)
        except Exception as e:
            print(f"Keep-Alive Ping Note: {e}")
        time.sleep(300)

threading.Thread(target=keep_alive, daemon=True).start()

# ================= TELEGRAM FUNCTIONS =================
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

# ================= TECHNICAL ANALYSIS =================
def get_candles(resolution="3m", limit=100):
    try:
        url = f"{BASE_URL}/v2/history/candles?resolution={resolution}&symbol={SYMBOL}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        if res.get("success"):
            df = pd.DataFrame(res["result"])
            for col in ["close", "high", "low", "open", "volume"]:
                df[col] = df[col].astype(float)
            return df
    except Exception as e:
        print(f"Candle fetch error ({resolution}): {e}")
    return None

def calculate_indicators():
    df_30m = get_candles(resolution="30m", limit=100) # 30M Trend Filter
    df_3m = get_candles(resolution="3m", limit=100)   # 3M Execution Chart
    
    if df_30m is None or df_3m is None or len(df_30m) < 50 or len(df_3m) < 50:
        return None

    # 1. 30M Higher Timeframe Trend (200 EMA Filter)
    df_30m["ema_200_30m"] = df_30m["close"].ewm(span=200, adjust=False).mean()
    is_30m_uptrend = float(df_30m["close"].iloc[-1]) > float(df_30m["ema_200_30m"].iloc[-1])
    is_30m_downtrend = float(df_30m["close"].iloc[-1]) < float(df_30m["ema_200_30m"].iloc[-1])

    # 2. 3M Execution Chart (21 EMA Pullback)
    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    
    latest_3m = df_3m.iloc[-1]
    prev_3m = df_3m.iloc[-2]

    current_price = float(latest_3m["close"])
    ema_21_val = float(latest_3m["ema_21_3m"])

    # Pullback Logic:
    # BUY: 30M Uptrend + 3M Price touches/crosses above 21 EMA (Pullback Completion)
    buy_pullback = is_30m_uptrend and (prev_3m["low"] <= prev_3m["ema_21_3m"]) and (latest_3m["close"] > ema_21_val)

    # SELL: 30M Downtrend + 3M Price touches/crosses below 21 EMA (Pullback Completion)
    sell_pullback = is_30m_downtrend and (prev_3m["high"] >= prev_3m["ema_21_3m"]) and (latest_3m["close"] < ema_21_val)

    return {
        "price": current_price,
        "ema_21_3m": ema_21_val,
        "is_30m_uptrend": is_30m_uptrend,
        "is_30m_downtrend": is_30m_downtrend,
        "buy_signal": buy_pullback,
        "sell_signal": sell_pullback
    }

# ================= TRADE MANAGEMENT =================
def check_active_position(current_price):
    global active_position
    if not active_position:
        return

    side = active_position["side"]
    entry = active_position["entry"]
    sl = active_position["sl"]
    tp = active_position["tp"]

    if side == "BUY":
        if current_price >= tp:
            msg = f"🎯 *TAKE PROFIT HIT! (WIN)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Profit:* +${TP_AMOUNT}"
            send_telegram(msg)
            active_position = None
        elif current_price <= sl:
            msg = f"🛑 *STOP LOSS HIT! (LOSS)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Loss:* -${SL_AMOUNT}"
            send_telegram(msg)
            active_position = None

    elif side == "SELL":
        if current_price <= tp:
            msg = f"🎯 *TAKE PROFIT HIT! (WIN)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Profit:* +${TP_AMOUNT}"
            send_telegram(msg)
            active_position = None
        elif current_price >= sl:
            msg = f"🛑 *STOP LOSS HIT! (LOSS)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Loss:* -${SL_AMOUNT}"
            send_telegram(msg)
            active_position = None

def execute_trade(side, price):
    global active_position
    sl = price - SL_AMOUNT if side == "BUY" else price + SL_AMOUNT
    tp = price + TP_AMOUNT if side == "BUY" else price - TP_AMOUNT
    
    active_position = {
        "side": side,
        "entry": price,
        "sl": sl,
        "tp": tp
    }

    emoji = "🚀" if side == "BUY" else "🔻"
    msg = (f"{emoji} *NEW 3M EMA 21 PULLBACK TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Side:* {side}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (-${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (+${TP_AMOUNT})\n"
           f"*Strategy:* 30M Trend Filter + 3M EMA 21 Pullback")
    
    print(f"Executing {side} Trade at {price}")
    send_telegram(msg)

# ================= FLASK SERVER ROUTES =================
@app.route('/')
def home():
    data = calculate_indicators()
    if data:
        price = data["price"]
        ema_21 = data["ema_21_3m"]
        is_30m_up = data["is_30m_uptrend"]
        buy_signal = data["buy_signal"]
        sell_signal = data["sell_signal"]

        check_active_position(price)

        if active_position is None:
            if buy_signal:
                execute_trade("BUY", price)
            elif sell_signal:
                execute_trade("SELL", price)

        return jsonify({
            "status": "running",
            "mode": "30M Trend + 3M EMA 21 Pullback",
            "price": price,
            "ema_21_3m": ema_21,
            "30m_uptrend": is_30m_up,
            "sl_setting": f"${SL_AMOUNT}",
            "tp_setting": f"${TP_AMOUNT}",
            "active_trade": active_position
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("⚡ *30M Trend + 3M EMA 21 Pullback Bot Started!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
