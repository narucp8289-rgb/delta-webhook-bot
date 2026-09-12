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

# Risk Management ($15 SL / $30 TP)
SL_AMOUNT = 15.0
TP_AMOUNT = 30.0

# Active Trade Tracker State
active_position = None
pulled_back = False  # Track pullback in 3M chart (price above 21 EMA)

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

def get_candles(resolution="3m", limit=250):
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

def find_recent_swing_low(df_3m):
    swing_lows = []
    for i in range(2, len(df_3m) - 1):
        if (df_3m['low'].iloc[i] < df_3m['low'].iloc[i-1] and 
            df_3m['low'].iloc[i] < df_3m['low'].iloc[i+1]):
            swing_lows.append(df_3m['low'].iloc[i])
            
    return swing_lows[-1] if swing_lows else df_3m['low'].iloc[-10:-1].min()

def calculate_strategy_conditions():
    global pulled_back

    # Fetch 30M and 3M Candles
    df_30m = get_candles(resolution="30m", limit=250)
    df_3m = get_candles(resolution="3m", limit=100)

    if df_30m is None or df_3m is None or len(df_30m) < 200 or len(df_3m) < 25:
        return None

    # 1. 30M Chart: 21 EMA & 200 EMA Filter
    df_30m["ema_21"] = df_30m["close"].ewm(span=21, adjust=False).mean()
    df_30m["ema_200"] = df_30m["close"].ewm(span=200, adjust=False).mean()
    
    # Check if 30M Close < 21 EMA AND 30M Close < 200 EMA (Strong Downtrend)
    is_30m_bearish = (df_30m["close"].iloc[-1] < df_30m["ema_21"].iloc[-1]) and \
                     (df_30m["close"].iloc[-1] < df_30m["ema_200"].iloc[-1])

    # 2. 3M Chart: 21 EMA & Pullback Check
    df_3m["ema_21"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["vol_avg"] = df_3m["volume"].rolling(20).mean()
    
    latest_3m = df_3m.iloc[-1]
    prev_3m = df_3m.iloc[-2]

    # Pullback check: Did price go above 21 EMA on 3M timeframe?
    if prev_3m["close"] > prev_3m["ema_21"] or latest_3m["high"] > latest_3m["ema_21"]:
        pulled_back = True

    # 3. Find Swing Low
    swing_low = find_recent_swing_low(df_3m)

    # 4. Filters: Candle Close Confirmation & Volume Spike Filter (1.2x)
    candle_closed_below_swing = latest_3m["close"] < swing_low
    closed_below_21ema = latest_3m["close"] < latest_3m["ema_21"]
    has_volume_spike = latest_3m["volume"] > (1.2 * latest_3m["vol_avg"])

    # Final Signal Status
    valid_breakdown = candle_closed_below_swing and closed_below_21ema and has_volume_spike

    return {
        "price": float(latest_3m["close"]),
        "is_30m_bearish": is_30m_bearish,
        "pulled_back": pulled_back,
        "valid_breakdown": valid_breakdown,
        "swing_low": float(swing_low),
        "volume_ok": has_volume_spike
    }

def check_active_position(current_price):
    global active_position
    if not active_position:
        return

    entry = active_position["entry"]
    sl = active_position["sl"]
    tp = active_position["tp"]

    if current_price <= tp:
        msg = f"🎯 *TAKE PROFIT HIT! (+30 Points)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Profit:* +${TP_AMOUNT}"
        send_telegram(msg)
        active_position = None
    elif current_price >= sl:
        msg = f"🛑 *STOP LOSS HIT! (-15 Points)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}\n*Loss:* -${SL_AMOUNT}"
        send_telegram(msg)
        active_position = None

def execute_trade(price):
    global active_position, pulled_back
    sl = price + SL_AMOUNT
    tp = price - TP_AMOUNT
    
    active_position = {
        "side": "SELL",
        "entry": price,
        "sl": sl,
        "tp": tp
    }
    pulled_back = False  # Reset pullback flag after entry

    msg = (f"🔻 *NEW 3M PULLBACK SELL TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Entry Price:* ${price}\n"
           f"*Stop Loss:* ${sl} (+${SL_AMOUNT})\n"
           f"*Take Profit:* ${tp} (-${TP_AMOUNT})\n"
           f"*Filters:* 30M 21/200 EMA + 3M 21 EMA Pullback + Volume Spike")
    
    print(f"Executing SELL Trade at {price}")
    send_telegram(msg)

@app.route('/')
def home():
    data = calculate_strategy_conditions()
    if data:
        price = data["price"]
        is_30m_bearish = data["is_30m_bearish"]
        pulled_back_status = data["pulled_back"]
        valid_breakdown = data["valid_breakdown"]
        swing_low = data["swing_low"]

        # Track active trade
        check_active_position(price)

        # Trigger Trade execution
        if active_position is None:
            if is_30m_bearish and pulled_back_status and valid_breakdown:
                execute_trade(price)

        return jsonify({
            "status": "running",
            "strategy": "30M (21/200 EMA) + 3M 21 EMA Pullback",
            "price": price,
            "is_30m_bearish": is_30m_bearish,
            "pulled_back": pulled_back_status,
            "recent_swing_low": swing_low,
            "sl_setting": f"+${SL_AMOUNT}",
            "tp_setting": f"-${TP_AMOUNT}",
            "active_trade": active_position
        })
    return jsonify({"status": "error fetching data"})

if __name__ == "__main__":
    send_telegram("⚡ *ETH 30M/3M EMA 21 Scalper Bot Online!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
