import os, time, hmac, hashlib, requests, json, threading
import pandas as pd
import numpy as np
from ta.trend import ADXIndicator, EMAIndicator
from ta.momentum import RSIIndicator
from flask import Flask

app = Flask(__name__)

API_KEY = "zemWuOQERwluGB0QmkgeFI1n4gnxC6"
API_SECRET = "rNmbnx4fOfsN6RYItSlJCtcehgJi3QfcDm0t13YRAci6rnoB0TF86XQfoco8"
BASE_URL = "https://cdn-ind.testnet.delta.exchange"
PRODUCT_ID = 1  # ETH/USD

SL_POINTS = 15.0
TP_POINTS = 30.0
position = None
entry_price = None

@app.route('/')
def home():
    return "Advanced Fib 0.5 Bot is Running!"

def generate_signature(method, endpoint, payload_str, timestamp):
    message = method + timestamp + endpoint + payload_str
    return hmac.new(API_SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

def send_order(action, size=1):
    global position, entry_price
    endpoint = "/v2/orders"
    timestamp = str(int(time.time()))
    payload = {
        "product_id": PRODUCT_ID,
        "size": size,
        "side": "buy" if action == "BUY" else "sell",
        "order_type": "market_order"
    }
    payload_str = json.dumps(payload)
    signature = generate_signature("POST", endpoint, payload_str, timestamp)
    headers = {
        'api-key': API_KEY,
        'signature': signature,
        'timestamp': timestamp,
        'Content-Type': 'application/json'
    }
    try:
        res = requests.post(BASE_URL + endpoint, data=payload_str, headers=headers)
        res_json = res.json()
        print(f"Order Executed [{action}]:", res_json)
        
        # Position track કરવા માટે
        if action in ["BUY", "SELL"]:
            position = action
        elif action in ["CLOSE_BUY", "CLOSE_SELL"]:
            position = None
            entry_price = None
    except Exception as e:
        print("Order Error:", e)

def fetch_candles(resolution="60"):
    url = f"{BASE_URL}/v2/chart/history?resolution={resolution}&symbol=ETHUSD"
    try:
        res = requests.get(url).json()
        if res.get("success"):
            df = pd.DataFrame(res["result"])
            df['h'] = df['h'].astype(float)
            df['l'] = df['l'].astype(float)
            df['c'] = df['c'].astype(float)
            return df
    except Exception as e:
        print(f"Candle Fetch Error ({resolution}):", e)
    return None

def bot_loop():
    global position, entry_price
    print("Advanced Strategy Loop Started...")
    
    while True:
        try:
            # 1-m અને 4-h કેન્ડલ્સ ફેચ કરવી
            df_1m = fetch_candles(resolution="60")
            df_4h = fetch_candles(resolution="240")

            if df_1m is not None and len(df_1m) >= 60 and df_4h is not None and len(df_4h) >= 55:
                
                # 1. 4H Trend Filter (51 EMA)
                ema_51_4h = EMAIndicator(close=df_4h['c'], window=51).ema_indicator().iloc[-1]
                current_close = df_1m['c'].iloc[-1]
                current_high = df_1m['h'].iloc[-1]
                current_low = df_1m['l'].iloc[-1]
                
                is_uptrend = current_close > ema_51_4h
                is_downtrend = current_close < ema_51_4h

                # 2. Fib 0.5 (50 Lookback)
                high_50 = df_1m['h'].iloc[-50:].max()
                low_50 = df_1m['l'].iloc[-50:].min()
                fib_500 = high_50 - ((high_50 - low_50) * 0.5)

                # 3. ADX & RSI (14 Period)
                adx_val = ADXIndicator(high=df_1m['h'], low=df_1m['l'], close=df_1m['c'], window=14).adx().iloc[-1]
                rsi_val = RSIIndicator(close=df_1m['c'], window=14).rsi().iloc[-1]

                # 4. Entry Conditions
                long_cond = is_uptrend and (current_low <= fib_500) and (current_close > fib_500) and (adx_val > 20) and (rsi_val > 48)
                short_cond = is_downtrend and (current_high >= fib_500) and (current_close < fib_500) and (adx_val > 20) and (rsi_val < 52)

                print(f"Price: {current_close} | Fib 0.5: {fib_500:.2f} | RSI: {rsi_val:.1f} | ADX: {adx_val:.1f} | Pos: {position}")

                # Trade Execution Logic
                if position is None:
                    if long_cond:
                        print(">>> Long Entry Conditions Met!")
                        entry_price = current_close
                        send_order("BUY")
                    elif short_cond:
                        print(">>> Short Entry Conditions Met!")
                        entry_price = current_close
                        send_order("SELL")

                # Risk Management (SL / TP / Break-Even)
                elif position == "BUY":
                    # Stop Loss or Take Profit Check
                    if current_close <= (entry_price - SL_POINTS) or current_close >= (entry_price + TP_POINTS):
                        print(">>> Closing BUY Position (SL/TP Hit)...")
                        send_order("CLOSE_BUY")

                elif position == "SELL":
                    # Stop Loss or Take Profit Check
                    if current_close >= (entry_price + SL_POINTS) or current_close <= (entry_price - TP_POINTS):
                        print(">>> Closing SELL Position (SL/TP Hit)...")
                        send_order("CLOSE_SELL")

        except Exception as e:
            print("Loop Exception:", e)

        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
