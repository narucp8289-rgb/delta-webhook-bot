import os
import requests
import pandas as pd
import numpy as np
import threading
import time
from datetime import datetime
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIGURATION =================
OKX_URL = "https://www.okx.com/api/v5/market/candles"
SYMBOLS = ["ETH-USDT", "BTC-USDT", "SOL-USDT", "XAUT-USDT"]

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

latest_market_data = {}
last_error = "None"
active_signals = {symbol: None for symbol in SYMBOLS}  
last_processed_candle_ts = {symbol: None for symbol in SYMBOLS}  

htf_cache = {}

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

def get_candles(symbol, bar="3m", limit=100):
    global last_error
    try:
        params = {"instId": symbol, "bar": bar, "limit": limit}
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(OKX_URL, params=params, headers=headers, timeout=4)
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
        last_error = f"Fetch exception ({symbol} - {bar}): {e}"
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

def get_cached_htf_trends(symbol):
    now = time.time()
    if symbol in htf_cache and (now - htf_cache[symbol]['time']) < 300:
        return htf_cache[symbol]['is_double_uptrend'], htf_cache[symbol]['is_double_downtrend'], htf_cache[symbol]['trend_1h'], htf_cache[symbol]['trend_30m']

    df_1h = get_candles(symbol, bar="1H", limit=50)
    df_30m = get_candles(symbol, bar="30m", limit=50)

    if df_1h is None or df_30m is None:
        return False, False, "DOWN", "DOWN"

    df_1h["ema_21_1h"] = df_1h["close"].ewm(span=21, adjust=False).mean()
    df_30m["ema_21_30m"] = df_30m["close"].ewm(span=21, adjust=False).mean()

    close_1h, ema_1h = float(df_1h["close"].iloc[-1]), float(df_1h["ema_21_1h"].iloc[-1])
    close_30m, ema_30m = float(df_30m["close"].iloc[-1]), float(df_30m["ema_21_30m"].iloc[-1])

    is_1h_uptrend = close_1h >= ema_1h
    is_30m_uptrend = close_30m >= ema_30m

    is_double_uptrend = is_1h_uptrend and is_30m_uptrend
    is_double_downtrend = (not is_1h_uptrend) and (not is_30m_uptrend)

    trend_1h = "UP" if is_1h_uptrend else "DOWN"
    trend_30m = "UP" if is_30m_uptrend else "DOWN"

    htf_cache[symbol] = {
        'is_double_uptrend': is_double_uptrend,
        'is_double_downtrend': is_double_downtrend,
        'trend_1h': trend_1h,
        'trend_30m': trend_30m,
        'time': now
    }

    return is_double_uptrend, is_double_downtrend, trend_1h, trend_30m

def find_proper_swings(df, lookback=20):
    """
    અગાઉની કેન્ડલ્સમાંથી Swing High / Low ગણવા (-3 થી પછળની કેન્ડલ્સ)
    """
    past_candles = df.iloc[-(lookback + 3):-3] 
    swing_high = float(past_candles["high"].max())
    swing_low = float(past_candles["low"].min())
    return swing_high, swing_low

def calculate_indicators(symbol):
    df_3m = get_candles(symbol, bar="3m", limit=100)
    if df_3m is None or len(df_3m) < 35:
        return None

    is_double_uptrend, is_double_downtrend, trend_1h, trend_30m = get_cached_htf_trends(symbol)

    df_3m["ema_21_3m"] = df_3m["close"].ewm(span=21, adjust=False).mean()
    df_3m["rsi"] = calculate_rsi(df_3m["close"], 14)
    df_3m["adx"] = calculate_adx(df_3m, 14)
    df_3m["atr"] = calculate_atr(df_3m, 14)
    df_3m["vol_avg"] = df_3m["volume"].rolling(window=20, min_periods=1).mean()

    # ✅ ૩ મિનિટની જે કેન્ડલ હમણાં જ ક્લોઝ થઈ છે તેને (-2) વડે લેવામાં આવે છે.
    entry_candle = df_3m.iloc[-2]
    candle_ts = str(entry_candle["ts"])

    entry_close = float(entry_candle["close"])
    entry_open = float(entry_candle["open"])
    entry_high = float(entry_candle["high"])
    entry_low = float(entry_candle["low"])
    
    ema_21_val = float(entry_candle["ema_21_3m"])
    rsi_val = float(entry_candle["rsi"])
    adx_val = float(entry_candle["adx"])
    atr_val = float(entry_candle["atr"])

    vol_val = float(entry_candle["volume"])
    vol_avg_val = float(entry_candle["vol_avg"])
    
    has_volume_spike_1_5x = vol_val >= (1.5 * vol_avg_val)
    has_strong_trend = adx_val >= 25.0
    
    candle_range = max(entry_high - entry_low, 1e-5)
    candle_body = abs(entry_close - entry_open)
    is_strong_body = (candle_body / candle_range) >= 0.60

    swing_high, swing_low = find_proper_swings(df_3m, lookback=20)

    recent_candles = df_3m.iloc[-22:-2]
    had_proper_pullback_up = sum(recent_candles["high"] > recent_candles["ema_21_3m"]) >= 1
    had_proper_pullback_down = sum(recent_candles["low"] < recent_candles["ema_21_3m"]) >= 1

    # ✅ કન્ડિશન ૧: ૩ મિનિટની કેન્ડલ ક્લોઝ થઈને Swing High તોડે તો જ BUY
    buy_signal = bool(
        is_double_uptrend and 
        had_proper_pullback_down and 
        (entry_close > swing_high) and # Swing High બ્રેક અને ક્લોઝ
        (entry_close > ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val > 52.0)
    )

    # ✅ કન્ડિશન ૨: ૩ મિનિટની કેન્ડલ ક્લોઝ થઈને Swing Low તોડે તો જ SELL
    sell_signal = bool(
        is_double_downtrend and 
        had_proper_pullback_up and 
        (entry_close < swing_low) and # Swing Low બ્રેક અને ક્લોઝ
        (entry_close < ema_21_val) and 
        has_volume_spike_1_5x and 
        has_strong_trend and 
        is_strong_body and 
        (rsi_val < 48.0)
    )

    return {
        "symbol": symbol,
        "candle_ts": candle_ts,
        "price": round(entry_close, 2),
        "high": round(entry_high, 2),
        "low": round(entry_low, 2),
        "trend_1h": trend_1h,
        "trend_30m": trend_30m,
        "ema_21_3m": round(ema_21_val, 2),
        "rsi": round(rsi_val, 2),
        "adx": round(adx_val, 2),
        "atr": round(atr_val, 2),
        "swing_low": round(swing_low, 2),
        "swing_high": round(swing_high, 2),
        "buy_signal": buy_signal,
        "sell_signal": sell_signal
    }

def alert_bot_loop():
    global latest_market_data, active_signals, last_processed_candle_ts
    while True:
        for symbol in SYMBOLS:
            try:
                data = calculate_indicators(symbol)
                if data:
                    latest_market_data[symbol] = data
                    price = data["price"]
                    high = data["high"]
                    low = data["low"]
                    atr = data["atr"]
                    current_candle_ts = data["candle_ts"]

                    # Active Orders Tracking (SL/TP)
                    if active_signals[symbol] is not None:
                        act = active_signals[symbol]
                        side = act["side"]
                        sl = act["sl"]
                        tp = act["tp"]
                        entry = act["entry"]

                        if side == "BUY":
                            if high >= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT! ({symbol} BUY)*\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif low <= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT! ({symbol} BUY)*\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                        elif side == "SELL":
                            if low <= tp:
                                send_telegram(f"🎯 *TAKE PROFIT HIT! ({symbol} SELL)*\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}")
                                active_signals[symbol] = None
                            elif high >= sl:
                                send_telegram(f"🛑 *STOP LOSS HIT! ({symbol} SELL)*\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}")
                                active_signals[symbol] = None

                    # ✅ ⚡ કેન્ડલ ક્લોઝ થતાં જ નો-ડિલે ઇન્સ્ટન્ટ મેસેજ
                    if active_signals[symbol] is None and last_processed_candle_ts[symbol] != current_candle_ts:
                        
                        if "BTC" in symbol:
                            sl_dist, tp_dist = 500.0, 1000.0
                        elif "ETH" in symbol:
                            sl_dist = round(max(atr * 2.0, 15.0), 2)
                            tp_dist = round(sl_dist * 2.0, 2)
                        elif "XAUT" in symbol:
                            sl_dist = round(max(atr * 2.0, 5.0), 2)
                            tp_dist = round(sl_dist * 2.0, 2)
                        else:  # SOL
                            sl_dist = round(max(atr * 2.0, 1.0), 2)
                            tp_dist = round(sl_dist * 2.0, 2)

                        if data["buy_signal"]:
                            sl = round(price - sl_dist, 2)
                            tp = round(price + tp_dist, 2)
                            active_signals[symbol] = {"side": "BUY", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts
                            
                            msg = (f"🚀 *CONFIRMED BUY SIGNAL ({symbol})*\n\n"
                                   f"📌 *Entry Price (Close):* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📉 *Broken Swing High:* ${data['swing_high']}\n\n"
                                   f"👉 *Action:* Delta Exchange માં **BUY (LONG)** કરો.")
                            send_telegram(msg)

                        elif data["sell_signal"]:
                            sl = round(price + sl_dist, 2)
                            tp = round(price - tp_dist, 2)
                            active_signals[symbol] = {"side": "SELL", "sl": sl, "tp": tp, "entry": price}
                            last_processed_candle_ts[symbol] = current_candle_ts

                            msg = (f"🔻 *CONFIRMED SELL SIGNAL ({symbol})*\n\n"
                                   f"📌 *Entry Price (Close):* ${price}\n"
                                   f"🛑 *Stop Loss:* ${sl}\n"
                                   f"🎯 *Take Profit:* ${tp}\n"
                                   f"📈 *Broken Swing Low:* ${data['swing_low']}\n\n"
                                   f"👉 *Action:* Delta Exchange માં **SELL (SHORT)** કરો.")
                            send_telegram(msg)

            except Exception as e:
                print(f"Loop Error ({symbol}): {e}")
            time.sleep(0.1) # ફાસ્ટ પ્રોસેસિંગ
        time.sleep(0.3)     # સતત સ્કેનિંગ

threading.Thread(target=alert_bot_loop, daemon=True).start()

@app.route('/')
def home():
    global latest_market_data, active_signals
    return jsonify({
        "status": "running",
        "mode": "Fast Candle-Close Confirmed Alert Bot",
        "active_signals": active_signals,
        "market_data": latest_market_data
    })

if __name__ == "__main__":
    send_telegram("⚡ *Fast Candle-Close Confirmed Bot Active*")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
