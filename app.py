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

# Updated Risk Management ($15 SL / $30 TP)
SL_AMOUNT = 15.0
TP_AMOUNT = 30.0

# Active Trade Tracker State
active_position = None

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

def get_candles(resolution="5m", limit=100):
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

def find_swing_levels(df):
    swing_highs, swing_lows = [], []
    for i in range(2, len(df) - 3):
        # 5-Candle Fractal High
        if (df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i-2] and 
            df['high'].iloc[i] > df['high'].iloc[i+1] and df['high'].iloc[i] > df['high'].iloc[i+2]):
            swing_highs.append(df['high'].iloc[i])
            
        # 5-Candle Fractal Low
        if (df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i-2] and 
            df['low'].iloc[i] < df['low'].iloc[i+1] and df['low'].iloc[i] < df['low'].iloc[i+2]):
            swing_lows.append(df['low'].iloc[i])
            
    recent_swing_high = swing_highs[-1] if swing_highs else df['high'].iloc[-20:-1].max()
    recent_swing_low = swing_lows[-1] if swing_lows else df['low'].iloc[-20:-1].min()
    return recent_swing_high, recent_swing_low

def calculate_indicators():
    df_1h = get_candles(resolution="1h", limit=100) # 1H Trend Filter
    df_5m = get_candles(resolution="5m", limit=100)  # 5M Primary Scalping Chart
    
    if df_1h is None or df_5m is None or len(df_1h) < 50 or len(df_5m) < 50:
        return None

    # 1. 1H Trend Filter (100 EMA)
    df_1h["ema_100_1h"] = df_1h["close"].ewm(span=100, adjust=False).mean()
    is_1h_uptrend = float(df_1h["close"].iloc[-1]) > float(df_1h["ema_100_1h"].iloc[-1])

    # 2. 5M Indicators (RSI & Volume Avg)
    df_5m["rsi"] = calculate_rsi(df_5m["close"], 14)
    df_5m["vol_avg"] = df_5m["volume"].rolling(window=20).mean()

    # 3. 5-Candle Fractal Swing High/Low
    swing_high, swing_low = find_swing_levels(df_5m)
    latest_5m = df_5m.iloc[-1]

    # 4. Volume Spike Filter (1.8x)
    has_volume_spike = float(latest_5m["volume"]) >= (1.8 * float(latest_5m["vol_avg"]))

    return {
        "price": float(latest_5m["close"]),
        "rsi": float(latest_5m["rsi"]),
        "volume_spike": has_volume_spike,
        "swing_high": float(swing_high),
        "swing_low": float(swing_low),
        "is_1h_uptrend": is_1h_uptrend
    }

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

    emoji = "⚡" if side == "BUY" else "🔻"
    msg = (f"{emoji} *NEW 5-MIN SCALP TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (-${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (+${TP_AMOUNT})\n"
           f"*Strategy:* 5M Scalp + 7-Layer Verification")
    
    print(f"Executing {side} Trade at {price}")
    send_telegram(msg)

@app.route('/')
def home():
    data = calculate_indicators()
    if data:
        price = data["price"]
        rsi = data["rsi"]
        vol_spike = data["volume_spike"]
        swing_high = data["swing_high"]
        swing_low = data["swing_low"]
        is_1h_uptrend = data["is_1h_uptrend"]

        # Track existing open trade
        check_active_position(price)

        # Check signals if no trade is active
        if active_position is None:
            # BUY Signal (1H Uptrend + 5M Candle Close > Swing High + 1.8x Volume + RSI > 55)
            if is_1h_uptrend and price > swing_high and vol_spike and rsi > 55:
                execute_trade("BUY", price)

            # SELL Signal (1H Downtrend + 5M Candle Close < Swing Low + 1.8x Volume + RSI < 45)
            elif (not is_1h_uptrend) and price < swing_low and vol_spike and rsi < 45:
                execute_trade("SELL", price)

        return jsonify({
            "status": "running",
            "mode": "5M Scalping",
            "price": price,
            "rsi": rsi,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "sl_setting": f"${SL_AMOUNT}",
            "tp_setting": f"${TP_AMOUNT}",
            "active_trade": active_position
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("⚡ *5-Min Scalping Bot Updated: $15 SL / $30 TP Set!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
