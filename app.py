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

# Active Trade Tracker State
active_position = None  # Stores: {"side": "BUY"/"SELL", "entry": price, "sl": price, "tp": price}

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

def find_swing_levels(df):
    """
    Finds the most recent valid Swing High and Swing Low using 5-candle Fractal logic.
    A candle is a Swing High if its high is higher than 2 candles before and 2 candles after it.
    """
    swing_highs = []
    swing_lows = []
    
    # We look through historical candles excluding the current live forming candle (-1)
    for i in range(2, len(df) - 3):
        # Swing High
        if (df['high'].iloc[i] > df['high'].iloc[i-1] and 
            df['high'].iloc[i] > df['high'].iloc[i-2] and 
            df['high'].iloc[i] > df['high'].iloc[i+1] and 
            df['high'].iloc[i] > df['high'].iloc[i+2]):
            swing_highs.append(df['high'].iloc[i])
            
        # Swing Low
        if (df['low'].iloc[i] < df['low'].iloc[i-1] and 
            df['low'].iloc[i] < df['low'].iloc[i-2] and 
            df['low'].iloc[i] < df['low'].iloc[i+1] and 
            df['low'].iloc[i] < df['low'].iloc[i+2]):
            swing_lows.append(df['low'].iloc[i])
            
    recent_swing_high = swing_highs[-1] if swing_highs else df['high'].iloc[-20:-1].max()
    recent_swing_low = swing_lows[-1] if swing_lows else df['low'].iloc[-20:-1].min()
    
    return recent_swing_high, recent_swing_low

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

    # Find Swing High/Low levels
    swing_high, swing_low = find_swing_levels(df_15m)

    latest_15m = df_15m.iloc[-1]

    # Volume Spike Check (1.5x of 20-period Average)
    has_volume_spike = float(latest_15m["volume"]) >= (1.5 * float(latest_15m["vol_avg"]))

    return {
        "price": float(latest_15m["close"]),
        "rsi": float(latest_15m["rsi"]),
        "volume_spike": has_volume_spike,
        "swing_high": float(swing_high),
        "swing_low": float(swing_low),
        "is_4h_uptrend": is_4h_uptrend
    }

def check_active_position(current_price):
    global active_position
    if not active_position:
        return

    side = active_position["side"]
    entry = active_position["entry"]
    sl = active_position["sl"]
    tp = active_position["tp"]

    # Check for BUY Position SL / TP
    if side == "BUY":
        if current_price >= tp:
            msg = f"🎯 *TAKE PROFIT HIT! (WIN)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Profit:* +${TP_AMOUNT}"
            send_telegram(msg)
            active_position = None
        elif current_price <= sl:
            msg = f"🛑 *STOP LOSS HIT! (LOSS)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Loss:* -${SL_AMOUNT}"
            send_telegram(msg)
            active_position = None

    # Check for SELL Position SL / TP
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
    msg = (f"{emoji} *NEW {side} TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (-${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (+${TP_AMOUNT})\n"
           f"*Strategy:* Smart Swing Breakout + Liquidity Filter")
    
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
        is_4h_uptrend = data["is_4h_uptrend"]

        # 1. Track SL/TP if there is an active trade
        check_active_position(price)

        # 2. Look for NEW trade signals only if NO trade is currently active
        if active_position is None:
            # BUY Signal (4H Uptrend + Candle Close > Swing High + Volume Spike + RSI > 50)
            if is_4h_uptrend and price > swing_high and vol_spike and rsi > 50:
                execute_trade("BUY", price)

            # SELL Signal (4H Downtrend + Candle Close < Swing Low + Volume Spike + RSI < 50)
            elif (not is_4h_uptrend) and price < swing_low and vol_spike and rsi < 50:
                execute_trade("SELL", price)

        return jsonify({
            "status": "running",
            "strategy": "Smart Swing Breakout (Fractal High/Low + Liquidity Filter)",
            "price": price,
            "rsi": rsi,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "active_trade": active_position
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("🤖 *Bot Updated with Smart Swing High/Low & Liquidity Filter!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
