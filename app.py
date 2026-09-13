import os
import requests
import pandas as pd
import threading
import time
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
BASE_URL = "https://api.delta.exchange"
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

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_indicators():
    df_30m = get_candles(resolution="30m", limit=100) # 30M Trend Filter
    df_3m = get_candles(resolution="3m", limit=100)   # 3M Execution Chart
    
    if df_30m is None or df_3m is None or len(df_30m) < 30 or len(df_3m) < 30:
        return None

    # 1. 30M Higher Timeframe Trend (21 EMA Filter)
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()
    is_30m_uptrend = float(df_30m["close"].iloc[-1]) > float(df_30m["ema_21_30m"].iloc[-1])
    is_30m_downtrend = float(df_30m["close"].iloc[-1]) < float(df_30m["ema_21_30m"].iloc[-1])

    # 2. 3M Indicators (21 EMA, Volume Spike, RSI)
    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20).mean()

    latest_3m = df_3m.iloc[-1]
    current_price = float(latest_3m["close"])
    ema_21_val = float(latest_3m["ema_21_3m"])
    rsi_val = float(latest_3m["rsi"])
    
    # Volume Filter (1.5x of 20-period average)
    has_volume_spike = float(latest_3m["volume"]) >= (1.5 * float(latest_3m["vol_avg"]))

    # 3. SWING BREAKOUT LOGIC (છેલ્લી 10 કેન્ડલ્સ)
    recent_candles = df_3m.iloc[-11:-1]
    
    # SELL: 30M Downtrend (21 EMA) + 3M EMA Pullback Up + Swing Low Breakdown + Volume Spike + RSI < 45
    had_pullback_up = any(recent_candles["high"] >= recent_candles["ema_21_3m"])
    swing_low = recent_candles["low"].min()
    sell_signal = (is_30m_downtrend and 
                   had_pullback_up and 
                   (current_price < swing_low) and 
                   (current_price < ema_21_val) and 
                   has_volume_spike and 
                   (rsi_val < 45))

    # BUY: 30M Uptrend (21 EMA) + 3M EMA Pullback Down + Swing High Breakout + Volume Spike + RSI > 55
    had_pullback_down = any(recent_candles["low"] <= recent_candles["ema_21_3m"])
    swing_high = recent_candles["high"].max()
    buy_signal = (is_30m_uptrend and 
                  had_pullback_down and 
                  (current_price > swing_high) and 
                  (current_price > ema_21_val) and 
                  has_volume_spike and 
                  (rsi_val > 55))

    return {
        "price": current_price,
        "ema_21_3m": ema_21_val,
        "rsi": rsi_val,
        "volume_spike": has_volume_spike,
        "is_30m_uptrend": is_30m_uptrend,
        "is_30m_downtrend": is_30m_downtrend,
        "swing_low": swing_low,
        "swing_high": swing_high,
        "buy_signal": buy_signal,
        "sell_signal": sell_signal
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
    msg = (f"{emoji} *NEW SWING BREAKOUT TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Side:* {side}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (-${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (+${TP_AMOUNT})\n"
           f"*Strategy:* 30M (21 EMA) + 3M Swing Breakdown + Volume Spike + RSI")
    
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
            "mode": "30M Trend (21 EMA) + 3M Swing Breakdown",
            "price": price,
            "ema_21_3m": ema_21,
            "rsi": data["rsi"],
            "volume_spike": data["volume_spike"],
            "30m_uptrend": is_30m_up,
            "swing_low": data["swing_low"],
            "swing_high": data["swing_high"],
            "sl_setting": f"${SL_AMOUNT}",
            "tp_setting": f"${TP_AMOUNT}",
            "active_trade": active_position
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("⚡ *30M 21 EMA Trend + 3M Swing Breakdown Bot Updated!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
