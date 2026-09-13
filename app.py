import os
import requests
import pandas as pd
import numpy as np
import threading
import time
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
# Reliable Public Market Data Endpoint for ETHUSD / ETHUSDT
BINANCE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "ETHUSDT"

# Telegram Configuration
TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# Risk Management ($15 SL / $30 TP)
SL_AMOUNT = 15.0
TP_AMOUNT = 30.0

# Active Trade Tracker State
active_position = None
latest_market_data = {}
last_error = "None"

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
def get_candles(interval="3m", limit=100):
    global last_error
    try:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "limit": limit
        }
        
        response = requests.get(BINANCE_URL, params=params, timeout=10)
        res = response.json()
        
        if isinstance(res, list) and len(res) > 0:
            # Kline Structure: [Open time, Open, High, Low, Close, Volume, ...]
            df = pd.DataFrame(res, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_asset_volume", "number_of_trades",
                "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
            ])

            # Convert numeric types explicitly
            for col in ["close", "high", "low", "open", "volume"]:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            df = df.dropna(subset=["close", "high", "low", "volume"]).reset_index(drop=True)
            return df
        else:
            last_error = f"API Error Response: {res}"
    except Exception as e:
        last_error = f"Fetch exception ({interval}): {e}"
        print(last_error)
    return None

def calculate_rsi(close_series, period=14):
    try:
        if len(close_series) < period + 1:
            return pd.Series([50.0] * len(close_series))

        delta = close_series.diff()
        gain = delta.clip(lower=0.0)
        loss = -1.0 * delta.clip(upper=0.0)

        avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi
    except Exception as e:
        print(f"RSI Calc Error: {e}")
        return pd.Series([50.0] * len(close_series))

def calculate_indicators():
    global last_error
    df_30m = get_candles(interval="30m", limit=100)
    df_3m = get_candles(interval="3m", limit=100)
    
    if df_30m is None or df_3m is None:
        return None

    if len(df_30m) < 25 or len(df_3m) < 25:
        last_error = f"Not enough candles: 30m={len(df_30m)}, 3m={len(df_3m)}"
        return None

    # 1. 30M Trend Filter (EMA 21)
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()
    close_30m = float(df_30m["close"].iloc[-1])
    ema_30m = float(df_30m["ema_21_30m"].iloc[-1])

    is_30m_uptrend = close_30m >= ema_30m
    is_30m_downtrend = close_30m < ema_30m

    # 2. 3M Indicators
    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20, min_periods=1).mean()

    latest_3m = df_3m.iloc[-1]
    current_price = float(latest_3m["close"])
    ema_21_val = float(latest_3m["ema_21_3m"])
    rsi_val = float(df_3m["rsi"].iloc[-1])
    
    # Volume Filter Check (1.2x 20-period volume average)
    vol_val = float(latest_3m["volume"])
    vol_avg_val = float(latest_3m["vol_avg"])
    has_volume_spike = vol_val >= (1.2 * vol_avg_val)

    # 3. SWING BREAKOUT LOGIC (Past 10 candles excluding live one)
    recent_candles = df_3m.iloc[-11:-1]
    swing_low = float(recent_candles["low"].min())
    swing_high = float(recent_candles["high"].max())

    had_pullback_up = any(recent_candles["high"] >= recent_candles["ema_21_3m"])
    sell_signal = bool(is_30m_downtrend and 
                       had_pullback_up and 
                       (current_price < swing_low) and 
                       (current_price < ema_21_val) and 
                       has_volume_spike and 
                       (rsi_val < 50.0))

    had_pullback_down = any(recent_candles["low"] <= recent_candles["ema_21_3m"])
    buy_signal = bool(is_30m_uptrend and 
                      had_pullback_down and 
                      (current_price > swing_high) and 
                      (current_price > ema_21_val) and 
                      has_volume_spike and 
                      (rsi_val > 50.0))

    last_error = "None"
    return {
        "price": round(current_price, 2),
        "ema_21_3m": round(ema_21_val, 2),
        "rsi": round(rsi_val, 2),
        "volume_spike": bool(has_volume_spike),
        "is_30m_uptrend": bool(is_30m_uptrend),
        "is_30m_downtrend": bool(is_30m_downtrend),
        "swing_low": round(swing_low, 2),
        "swing_high": round(swing_high, 2),
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
           f"*Strategy:* 30M (21 EMA) + 3M Swing Breakdown (Volume 1.2x + RSI 50)")
    
    send_telegram(msg)

# ================= BACKGROUND AUTO-SCANNER LOOP =================
def trading_bot_loop():
    global latest_market_data
    while True:
        try:
            data = calculate_indicators()
            if data:
                latest_market_data = data
                price = data["price"]
                buy_signal = data["buy_signal"]
                sell_signal = data["sell_signal"]

                check_active_position(price)

                if active_position is None:
                    if buy_signal:
                        execute_trade("BUY", price)
                    elif sell_signal:
                        execute_trade("SELL", price)
        except Exception as e:
            print(f"Loop Error: {e}")
        
        time.sleep(10)

threading.Thread(target=trading_bot_loop, daemon=True).start()

# ================= FLASK SERVER ROUTES =================
@app.route('/')
def home():
    global latest_market_data
    data = calculate_indicators()
    if data:
        latest_market_data = data

    return jsonify({
        "status": "running",
        "mode": "Continuous 10s Scanner",
        "market_data": latest_market_data,
        "active_trade": active_position,
        "debug_error": last_error
    })

if __name__ == "__main__":
    send_telegram("⚡ *Bot Fixed: 100% Reliable Kline Feed Connected & RSI Active!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
