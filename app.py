import os
import requests
import pandas as pd
import numpy as np
import threading
import time
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
OKX_URL = "https://www.okx.com/api/v5/market/candles"
SYMBOL = "ETH-USDT"

# Telegram Configuration
TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

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
def get_candles(bar="3m", limit=100):
    global last_error
    try:
        params = {"instId": SYMBOL, "bar": bar, "limit": limit}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(OKX_URL, params=params, headers=headers, timeout=10)
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
            else:
                last_error = "Empty data returned from OKX"
        else:
            last_error = f"OKX API Error: {res.get('msg')}"
    except Exception as e:
        last_error = f"Fetch exception ({bar}): {e}"
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
        return 100.0 - (100.0 / (1.0 + rs))
    except Exception as e:
        print(f"RSI Calc Error: {e}")
        return pd.Series([50.0] * len(close_series))

def calculate_adx(df, period=14):
    try:
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        df['+dm'] = np.where((df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']), np.maximum(df['high'] - df['high'].shift(1), 0), 0)
        df['-dm'] = np.where((df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)), np.maximum(df['low'].shift(1) - df['low'], 0), 0)

        tr_smooth = df['tr'].ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        plus_di = 100 * (df['+dm'].ewm(alpha=1/period, min_periods=period, adjust=False).mean() / (tr_smooth + 1e-10))
        minus_di = 100 * (df['-dm'].ewm(alpha=1/period, min_periods=period, adjust=False).mean() / (tr_smooth + 1e-10))

        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        return adx.fillna(0)
    except Exception as e:
        print(f"ADX Error: {e}")
        return pd.Series([0.0] * len(df))

def calculate_atr(df, period=14):
    try:
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        return df['tr'].rolling(window=period).mean().fillna(15.0)
    except Exception as e:
        print(f"ATR Error: {e}")
        return pd.Series([15.0] * len(df))

def calculate_indicators():
    global last_error
    df_1h = get_candles(bar="1H", limit=100)
    df_30m = get_candles(bar="30m", limit=100)
    df_3m = get_candles(bar="3m", limit=100)
    
    if df_1h is None or df_30m is None or df_3m is None:
        return None

    if len(df_1h) < 25 or len(df_30m) < 25 or len(df_3m) < 25:
        last_error = f"Not enough candles"
        return None

    # 1. 1H & 30M Double Trend Filter
    df_1h["ema_21_1h"] = df_1h["close"].ewm(span=21, adjust=False).mean()
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()

    close_1h = float(df_1h["close"].iloc[-1])
    ema_1h = float(df_1h["ema_21_1h"].iloc[-1])
    
    close_30m = float(df_30m["close"].iloc[-1])
    ema_30m = float(df_30m["ema_21_30m"].iloc[-1])

    # 1H અને 30M ટ્રેન્ડ સ્ટેટસ
    is_1h_uptrend = close_1h >= ema_1h
    is_30m_uptrend = close_30m >= ema_30m
    
    is_double_uptrend = is_1h_uptrend and is_30m_uptrend
    is_double_downtrend = (not is_1h_uptrend) and (not is_30m_uptrend)

    # 2. 3M Indicators
    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["adx"] = calculate_adx(df_3m, 14)
    df_3m["atr"] = calculate_atr(df_3m, 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20, min_periods=1).mean()

    # 3. Last Closed Candle Confirmation
    last_closed_3m = df_3m.iloc[-2]
    closed_price = float(last_closed_3m["close"])
    ema_21_val = float(last_closed_3m["ema_21_3m"])
    rsi_val = float(last_closed_3m["rsi"])
    adx_val = float(last_closed_3m["adx"])
    atr_val = float(last_closed_3m["atr"])

    vol_val = float(last_closed_3m["volume"])
    vol_avg_val = float(last_closed_3m["vol_avg"])
    
    has_volume_spike = vol_val >= (1.2 * vol_avg_val)
    has_strong_trend = adx_val >= 25.0  # STRICT ADX FILTER (>= 25)

    # 4. Pure Swing Logic
    recent_candles = df_3m.iloc[-25:-2]

    candles_completely_below = recent_candles[recent_candles["high"] < recent_candles["ema_21_3m"]]
    swing_low = float(candles_completely_below["low"].min()) if not candles_completely_below.empty else float(recent_candles["low"].min())

    candles_completely_above = recent_candles[recent_candles["low"] > recent_candles["ema_21_3m"]]
    swing_high = float(candles_completely_above["high"].max()) if not candles_completely_above.empty else float(recent_candles["high"].max())

    had_proper_pullback_up = sum(recent_candles["high"] > recent_candles["ema_21_3m"]) >= 1
    had_proper_pullback_down = sum(recent_candles["low"] < recent_candles["ema_21_3m"]) >= 1

    # HIGH-QUALITY SIGNALS
    sell_signal = bool(is_double_downtrend and 
                       had_proper_pullback_up and 
                       (closed_price < swing_low) and 
                       (closed_price < ema_21_val) and 
                       has_volume_spike and 
                       has_strong_trend and 
                       (rsi_val < 50.0))

    buy_signal = bool(is_double_uptrend and 
                      had_proper_pullback_down and 
                      (closed_price > swing_high) and 
                      (closed_price > ema_21_val) and 
                      has_volume_spike and 
                      has_strong_trend and 
                      (rsi_val > 50.0))

    last_error = "None"
    return {
        "price": round(closed_price, 2),
        "trend_1h": "UP" if is_1h_uptrend else "DOWN",
        "trend_30m": "UP" if is_30m_uptrend else "DOWN",
        "ema_21_3m": round(ema_21_val, 2),
        "rsi": round(rsi_val, 2),
        "adx": round(adx_val, 2),
        "atr": round(atr_val, 2),
        "volume_spike": bool(has_volume_spike),
        "strong_trend": bool(has_strong_trend),
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
            send_telegram(f"🎯 *TAKE PROFIT HIT! (WIN)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}")
            active_position = None
        elif current_price <= sl:
            send_telegram(f"🛑 *STOP LOSS HIT! (LOSS)*\n\n*Symbol:* {SYMBOL}\n*Side:* BUY\n*Entry:* ${entry}\n*Exit:* ${current_price}")
            active_position = None

    elif side == "SELL":
        if current_price <= tp:
            send_telegram(f"🎯 *TAKE PROFIT HIT! (WIN)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}")
            active_position = None
        elif current_price >= sl:
            send_telegram(f"🛑 *STOP LOSS HIT! (LOSS)*\n\n*Symbol:* {SYMBOL}\n*Side:* SELL\n*Entry:* ${entry}\n*Exit:* ${current_price}")
            active_position = None

def execute_trade(side, price, atr):
    global active_position
    sl_dist = round(max(atr * 2.0, 15.0), 2)  # High-Quality Safe Dynamic SL
    tp_dist = round(sl_dist * 2.0, 2)          # 1:2 Risk to Reward

    sl = round(price - sl_dist if side == "BUY" else price + sl_dist, 2)
    tp = round(price + tp_dist if side == "BUY" else price - tp_dist, 2)
    
    active_position = {"side": side, "entry": price, "sl": sl, "tp": tp}

    emoji = "🚀" if side == "BUY" else "🔻"
    msg = (f"{emoji} *A+ GRADE HIGH-QUALITY TRADE EXECUTED!*\n\n"
           f"*Symbol:* {SYMBOL}\n"
           f"*Side:* {side}\n"
           f"*Entry Price:* ${price}\n"
           f"*Safe Dynamic SL (2x ATR):* ${sl} (-${sl_dist})\n"
           f"*Dynamic TP (1:2 R:R):* ${tp} (+${tp_dist})\n"
           f"*Strategy:* 1H+30M Double Trend + ADX 25+ Filter Active")
    send_telegram(msg)

# ================= BACKGROUND SCANNER LOOP =================
def trading_bot_loop():
    global latest_market_data
    while True:
        try:
            data = calculate_indicators()
            if data:
                latest_market_data = data
                price = data["price"]
                atr = data["atr"]

                check_active_position(price)

                if active_position is None:
                    if data["buy_signal"]:
                        execute_trade("BUY", price, atr)
                    elif data["sell_signal"]:
                        execute_trade("SELL", price, atr)
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
        "mode": "A+ Grade High Quality Setup Scanner",
        "market_data": latest_market_data,
        "active_trade": active_position,
        "debug_error": last_error
    })

if __name__ == "__main__":
    send_telegram("⚡ *Bot Updated: 1H & 30M Trend Display Enabled in JSON Output!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
