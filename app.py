import os
import requests
import pandas as pd
import numpy as np
import threading
import time
import hmac
import hashlib
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
OKX_URL = "https://www.okx.com/api/v5/market/candles"
SYMBOL_OKX = "ETH-USDT"
SYMBOL_DELTA = "ETHUSDT"

DELTA_BASE_URL = "https://demo-api.delta.exchange"
DELTA_API_KEY = "AJoKtFdK8Zk6RGERPVgL7JKsLqmZlM"
DELTA_API_SECRET = "kBhaicd31lPXRulniI5Y5r8M2S4A012O7Wfoea9y6jXDkFGbzfv59TTSebsl"

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

active_position = None
latest_market_data = {}
last_error = "None"
cached_product_id = None

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

def generate_delta_signature(method, path, payload="", timestamp=""):
    signature_data = method + timestamp + path + payload
    return hmac.new(
        DELTA_API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def send_delta_request(method, path, payload=None):
    try:
        url = DELTA_BASE_URL + path
        timestamp = str(int(time.time()))
        body_str = str(payload) if payload else ""
        
        signature = generate_delta_signature(method, path, body_str, timestamp)
        headers = {
            "api-key": DELTA_API_KEY,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json"
        }
        
        if method == "GET":
            res = requests.get(url, headers=headers, timeout=5)
        else:
            res = requests.post(url, headers=headers, json=payload, timeout=5)
        return res.json()
    except Exception as e:
        print(f"Delta API Error: {e}")
        return None

def get_delta_product_id():
    global cached_product_id
    if cached_product_id:
        return cached_product_id

    prod_res = send_delta_request("GET", "/v2/products")
    if prod_res and "result" in prod_res:
        products = prod_res["result"]
        if isinstance(products, list):
            for p in products:
                symbol = p.get("symbol") or p.get("product_specs", {}).get("symbol")
                if symbol == SYMBOL_DELTA or symbol == "ETH-USDT":
                    cached_product_id = p["id"]
                    return cached_product_id
    return None

def place_delta_order(side, price, sl_price, tp_price):
    global active_position
    try:
        product_id = get_delta_product_id()
        
        if not product_id:
            send_telegram("❌ Order Failed: Delta Product ID Not Found")
            return

        order_size = 1

        order_payload = {
            "product_id": product_id,
            "size": order_size,
            "side": side.lower(),
            "order_type": "market_order"
        }
        
        res = send_delta_request("POST", "/v2/orders", order_payload)
        
        if res and res.get("success"):
            sl_payload = {
                "product_id": product_id,
                "size": order_size,
                "side": "sell" if side == "BUY" else "buy",
                "order_type": "stop_market_order",
                "stop_price": str(sl_price),
                "reduce_only": True
            }
            send_delta_request("POST", "/v2/orders", sl_payload)

            tp_payload = {
                "product_id": product_id,
                "size": order_size,
                "side": "sell" if side == "BUY" else "buy",
                "order_type": "take_profit_market_order",
                "stop_price": str(tp_price),
                "reduce_only": True
            }
            send_delta_request("POST", "/v2/orders", tp_payload)

            active_position = {"side": side, "entry": price, "sl": sl_price, "tp": tp_price}

            emoji = "🚀" if side == "BUY" else "🔻"
            msg = (f"{emoji} *HARD SL ORDER EXECUTED ON DELTA DEMO!*\n\n"
                   f"*Symbol:* {SYMBOL_DELTA}\n"
                   f"*Side:* {side}\n"
                   f"*Entry Price:* ${price}\n"
                   f"*Hard Stop Loss:* ${sl_price}\n"
                   f"*Hard Take Profit:* ${tp_price}\n"
                   f"*Execution:* Instant Exchange Hard SL")
            send_telegram(msg)
        else:
            send_telegram(f"❌ Delta Order Failed: {res}")
    except Exception as e:
        print(f"Execution Error: {e}")

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
    global last_error
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
    ema_21_val = float(last_closed_3m["ema_21_3m"])
    rsi_val = float(last_closed_3m["rsi"])
    adx_val = float(last_closed_3m["adx"])
    atr_val = float(last_closed_3m["atr"])

    vol_val = float(last_closed_3m["volume"])
    vol_avg_val = float(last_closed_3m["vol_avg"])
    
    has_volume_spike = vol_val >= (1.2 * vol_avg_val)
    has_strong_trend = adx_val >= 25.0

    recent_candles = df_3m.iloc[-25:-2]
    swing_low = float(recent_candles["low"].min())
    swing_high = float(recent_candles["high"].max())

    had_proper_pullback_up = sum(recent_candles["high"] > recent_candles["ema_21_3m"]) >= 1
    had_proper_pullback_down = sum(recent_candles["low"] < recent_candles["ema_21_3m"]) >= 1

    sell_signal = bool(is_double_downtrend and had_proper_pullback_up and (closed_price < swing_low) and (closed_price < ema_21_val) and has_volume_spike and has_strong_trend and (rsi_val < 50.0))
    buy_signal = bool(is_double_uptrend and had_proper_pullback_down and (closed_price > swing_high) and (closed_price > ema_21_val) and has_volume_spike and has_strong_trend and (rsi_val > 50.0))

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

def trading_bot_loop():
    global latest_market_data
    while True:
        try:
            data = calculate_indicators()
            if data:
                latest_market_data = data
                price = data["price"]
                atr = data["atr"]

                if active_position is None:
                    sl_dist = round(max(atr * 2.0, 15.0), 2)
                    tp_dist = round(sl_dist * 2.0, 2)

                    if data["buy_signal"]:
                        sl = round(price - sl_dist, 2)
                        tp = round(price + tp_dist, 2)
                        place_delta_order("BUY", price, sl, tp)

                    elif data["sell_signal"]:
                        sl = round(price + sl_dist, 2)
                        tp = round(price - tp_dist, 2)
                        place_delta_order("SELL", price, sl, tp)
        except Exception as e:
            print(f"Loop Error: {e}")
        time.sleep(2)

threading.Thread(target=trading_bot_loop, daemon=True).start()

@app.route('/')
def home():
    global latest_market_data
    data = calculate_indicators()
    if data:
        latest_market_data = data
    return jsonify({
        "status": "running",
        "mode": "Delta Live Hard SL Trading Active",
        "market_data": latest_market_data,
        "active_trade": active_position,
        "debug_error": last_error
    })

if __name__ == "__main__":
    send_telegram("🚀 *Bot Updated: Product ID Resolution Fixed!*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
