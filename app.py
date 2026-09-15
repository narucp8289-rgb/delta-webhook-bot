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
SYMBOL_OKX = "ETH-USDT"

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

latest_market_data = {}
last_error = "None"
last_signal_time = 0

# એક્ટિવ ટ્રેડ ટ્રેકિંગ માટે
active_signal = None  # Stores: {'side': 'BUY'/'SELL', 'sl': float, 'tp': float, 'entry': float}

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

def get_candles(bar="3m", limit=100):
    global last_error
    try:
        params = {"instId": SYMBOL_OKX, "bar": bar, "limit": limit}
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
    except Exception as e:
        last_error = f"Fetch exception ({bar}): {e}"
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
        return pd.Series([0.0] * len(df))

def calculate_atr(df, period=14):
    try:
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
        )
        return df['tr'].rolling(window=period).mean().fillna(15.0)
    except Exception as e:
        return pd.Series([15.0] * len(df))

def calculate_indicators():
    df_1h = get_candles(bar="1H", limit=100)
    df_30m = get_candles(bar="30m", limit=100)
    df_3m = get_candles(bar="3m", limit=100)
    
    if df_1h is None or df_30m is None or df_3m is None:
        return None

    df_1h["ema_21_1h"] = df_1h["close"].ewm(span=21, adjust=False).mean()
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()

    close_1h, ema_1h = float(df_1h["close"].iloc[-1]), float(df_1h["ema_21_1h"].iloc[-1])
    close_30m, ema_30m = float(df_30m["close"].iloc[-1]), float(df_30m["ema_21_30m"].iloc[-1])

    is_1h_uptrend = close_1h >= ema_1h
    is_30m_uptrend = close_30m >= ema_30m
    
    is_double_uptrend = is_1h_uptrend and is_30m_uptrend
    is_double_downtrend = (not is_1h_uptrend) and (not is_30m_uptrend)

    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["adx"] = calculate_adx(df_3m, 14)
    df_3m["atr"] = calculate_atr(df_3m, 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20, min_periods=1).mean()

    last_closed_3m = df_3m.iloc[-2]
    closed_price = float(last_closed_3m["close"])
    open_price = float(last_closed_3m["open"])
    high_price = float(last_closed_3m["high"])
    low_price = float(last_closed_3m["low"])
    
    ema_21_val = float(last_closed_3m["ema_21_3m"])
    rsi_val = float(last_closed_3m["rsi"])
    adx_val = float(last_closed_3m["adx"])
    atr_val = float(last_closed_3m["atr"])

    vol_val = float(last_closed_3m["volume"])
    vol_avg_val = float(last_closed_3m["vol_avg"])
    
    has_volume_spike_1_5x = vol_val >= (1.5 * vol_avg_val)
    has_strong_trend = adx_val >= 25.0
    
    candle_range = max(high_price - low_price, 1e-5)
    candle_body = abs(closed_price - open_price)
    is_strong_body = (candle_body / candle_range) >= 0.60

    recent_candles = df_3m.iloc[-25:-2]
    swing_low = float(recent_candles["low"].min())
    swing_high = float(recent_candles["high"].max())

    had_proper_pullback_up = sum(recent_candles["high"] > recent_candles["ema_21_3m"]) >= 1
    had_proper_pullback_down = sum(recent_candles["low"] < recent_candles["ema_21_3m"]) >= 1

    sell_signal = bool(
        is_double_downtrend and 
        had_proper_pullback_up and 
        (closed_price < swing_low) and 
        (closed_price < ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val < 48.0)
    )
    
    buy_signal = bool(
        is_double_uptrend and 
        had_proper_pullback_down and 
        (closed_price > swing_high) and 
        (closed_price > ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val > 52.0)
    )

    return {
        "price": round(closed_price, 2),
        "high": round(high_price, 2),
        "low": round(low_price, 2),
        "trend_1h": "UP" if is_1h_uptrend else "DOWN",
        "trend_30m": "UP" if is_30m_uptrend else "DOWN",
        "ema_21_3m": round(ema_21_val, 2),
        "rsi": round(rsi_val, 2),
        "adx": round(adx_val, 2),
        "atr": round(atr_val, 2),
        "volume_spike_1_5x": bool(has_volume_spike_1_5x),
        "strong_body_60pct": bool(is_strong_body),
        "strong_trend": bool(has_strong_trend),
        "swing_low": round(swing_low, 2),
        "swing_high": round(swing_high, 2),
        "buy_signal": buy_signal,
        "sell_signal": sell_signal
    }

def alert_bot_loop():
    global latest_market_data, last_signal_time, active_signal
    while True:
        try:
            data = calculate_indicators()
            if data:
                latest_market_data = data
                price = data["price"]
                high = data["high"]
                low = data["low"]
                atr = data["atr"]
                current_time = time.time()

                # === 1. SL/TP TRACKING LOGIC ===
                if active_signal is not None:
                    side = active_signal["side"]
                    sl = active_signal["sl"]
                    tp = active_signal["tp"]
                    entry = active_signal["entry"]

                    if side == "BUY":
                        if high >= tp:
                            msg = f"🎯 *TAKE PROFIT HIT! (BUY)*\n\n📌 Entry: ${entry}\n🎯 TP Target: ${tp}\n✅ Profit Achieved!"
                            send_telegram(msg)
                            active_signal = None
                        elif low <= sl:
                            msg = f"🛑 *STOP LOSS HIT! (BUY)*\n\n📌 Entry: ${entry}\n🛑 SL Triggered: ${sl}"
                            send_telegram(msg)
                            active_signal = None

                    elif side == "SELL":
                        if low <= tp:
                            msg = f"🎯 *TAKE PROFIT HIT! (SELL)*\n\n📌 Entry: ${entry}\n🎯 TP Target: ${tp}\n✅ Profit Achieved!"
                            send_telegram(msg)
                            active_signal = None
                        elif high >= sl:
                            msg = f"🛑 *STOP LOSS HIT! (SELL)*\n\n📌 Entry: ${entry}\n🛑 SL Triggered: ${sl}"
                            send_telegram(msg)
                            active_signal = None

                # === 2. NEW SIGNAL GENERATION LOGIC ===
                # જૂનો ટ્રેડ ચાલુ ન હોય ત્યારે જ નવો ટ્રેડ આપશે
                if active_signal is None and (current_time - last_signal_time) > 300:
                    sl_dist = round(max(atr * 2.0, 15.0), 2)
                    tp_dist = round(sl_dist * 2.0, 2)

                    if data["buy_signal"]:
                        sl = round(price - sl_dist, 2)
                        tp = round(price + tp_dist, 2)
                        active_signal = {"side": "BUY", "sl": sl, "tp": tp, "entry": price}
                        
                        msg = (f"🚀 *HIGH-ACCURACY BUY SIGNAL (ETH-USDT)*\n\n"
                               f"📌 *Entry Price:* ${price}\n"
                               f"🛑 *Stop Loss:* ${sl}\n"
                               f"🎯 *Take Profit:* ${tp}\n"
                               f"📉 *Swing High:* ${data['swing_high']}\n\n"
                               f"👉 *Action:* Delta Exchange માં **BUY (LONG)** કરો.")
                        send_telegram(msg)
                        last_signal_time = current_time

                    elif data["sell_signal"]:
                        sl = round(price + sl_dist, 2)
                        tp = round(price - tp_dist, 2)
                        active_signal = {"side": "SELL", "sl": sl, "tp": tp, "entry": price}

                        msg = (f"🔻 *HIGH-ACCURACY SELL SIGNAL (ETH-USDT)*\n\n"
                               f"📌 *Entry Price:* ${price}\n"
                               f"🛑 *Stop Loss:* ${sl}\n"
                               f"🎯 *Take Profit:* ${tp}\n"
                               f"📈 *Swing Low:* ${data['swing_low']}\n\n"
                               f"👉 *Action:* Delta Exchange માં **SELL (SHORT)** કરો.")
                        send_telegram(msg)
                        last_signal_time = current_time

        except Exception as e:
            print(f"Loop Error: {e}")
        time.sleep(3)

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    global latest_market_data, active_signal
    data = calculate_indicators()
    if data:
        latest_market_data = data
    return jsonify({
        "status": "running",
        "mode": "Telegram Alert Bot with Live SL/TP Tracker",
        "active_signal": active_signal,
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    send_telegram("🔔 *Manual Alert Bot Active with Live SL/TP Tracker!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
