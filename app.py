import os
import time
import requests
import pandas as pd
import numpy as np
import hmac
import hashlib
from flask import Flask
from threading import Thread

# ==========================================
# 1. CONFIGURATION & NEW API CREDENTIALS
# ==========================================
API_KEY = "JRbVTfMgzkMENWh3fvy074F2JH95Fm"
API_SECRET = "y4yLw2wzHIruaHcXb4QP6j20n8VCvsqc25HTJClrjA2vlUv5efqQF3ZuM8MJ"
BASE_URL = "https://api.testnet.delta.exchange"
SYMBOL = "ETHUSD"
LOOKBACK = 50
STOP_LOSS_PTS = 15.0
TAKE_PROFIT_PTS = 30.0

# Active Trade Tracking
in_position = False
position_side = None
entry_price = 0.0

# ==========================================
# 2. FLASK SERVER FOR RENDER PINGER
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "Advanced Fib 0.5 Bot is Running!"

def run_flask():
    app.run(host='0.0.0.0', port=10000)

# ==========================================
# 3. HELPER FUNCTIONS & INDICATORS
# ==========================================
def generate_signature(method, path, payload=""):
    timestamp = str(int(time.time()))
    signature_data = method + timestamp + path + payload
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp

def fetch_candles(symbol, resolution="1m", limit=100):
    url = f"{BASE_URL}/v2/history/candles?resolution={resolution}&symbol={symbol}&limit={limit}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get("success"):
            df = pd.DataFrame(data["result"])
            df = df.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'})
            df['Close'] = df['Close'].astype(float)
            df['High'] = df['High'].astype(float)
            df['Low'] = df['Low'].astype(float)
            return df
    except Exception as e:
        print(f"Candle fetch error: {e}")
    return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_adx(df, period=14):
    df = df.copy()
    df['tr'] = np.maximum(df['High'] - df['Low'], 
                np.maximum(abs(df['High'] - df['Close'].shift(1)), 
                           abs(df['Low'] - df['Close'].shift(1))))
    df['plus_dm'] = np.where((df['High'] - df['High'].shift(1)) > (df['Low'].shift(1) - df['Low']), 
                             np.maximum(df['High'] - df['High'].shift(1), 0), 0)
    df['minus_dm'] = np.where((df['Low'].shift(1) - df['Low']) > (df['High'] - df['High'].shift(1)), 
                              np.maximum(df['Low'].shift(1) - df['Low'], 0), 0)
    
    tr_smooth = df['tr'].rolling(window=period).sum()
    plus_di = 100 * (df['plus_dm'].rolling(window=period).sum() / tr_smooth)
    minus_di = 100 * (df['minus_dm'].rolling(window=period).sum() / tr_smooth)
    
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx = dx.rolling(window=period).mean()
    return adx

def place_order(side, size=1):
    path = "/v2/orders"
    payload = f'{{"product_symbol":"{SYMBOL}","size":{size},"order_type":"market_order","side":"{side}"}}'
    sig, ts = generate_signature("POST", path, payload)
    headers = {
        'api-key': API_KEY,
        'signature': sig,
        'timestamp': ts,
        'Content-Type': 'application/json'
    }
    try:
        res = requests.post(BASE_URL + path, data=payload, headers=headers, timeout=10)
        print(f"Order Execution Result ({side}):", res.json())
        return res.json()
    except Exception as e:
        print(f"Order Placement Error: {e}")
        return None

# ==========================================
# 4. STRATEGY LOOP
# ==========================================
def strategy_loop():
    global in_position, position_side, entry_price
    print("Advanced Strategy Loop Started with NEW API Keys...")
    
    while True:
        try:
            df = fetch_candles(SYMBOL, resolution="1m", limit=100)
            df_4h = fetch_candles(SYMBOL, resolution="4h", limit=100)
            
            if df is not None and df_4h is not None and len(df) >= LOOKBACK:
                current_price = df['Close'].iloc[-1]
                
                # Indicators
                df_4h['EMA51'] = df_4h['Close'].ewm(span=51, adjust=False).mean()
                ema_4h = df_4h['EMA51'].iloc[-1]
                
                df['RSI'] = calculate_rsi(df['Close'], 14)
                rsi = df['RSI'].iloc[-1]
                
                df['ADX'] = calculate_adx(df, 14)
                adx = df['ADX'].iloc[-1]
                
                recent_high = df['High'].iloc[-LOOKBACK:].max()
                recent_low = df['Low'].iloc[-LOOKBACK:].min()
                fib_05 = recent_low + 0.5 * (recent_high - recent_low)
                
                prev_price = df['Close'].iloc[-2]

                print(f"Price: {current_price:.2f} | Fib 0.5: {fib_05:.2f} | RSI: {rsi:.1f} | ADX: {adx:.1f} | Pos: {position_side}")

                # Position Management (SL/TP Check)
                if in_position:
                    if position_side == "buy":
                        if current_price <= (entry_price - STOP_LOSS_PTS) or current_price >= (entry_price + TAKE_PROFIT_PTS):
                            print("Closing Long Position...")
                            place_order("sell")
                            in_position = False
                            position_side = None
                    elif position_side == "sell":
                        if current_price >= (entry_price + STOP_LOSS_PTS) or current_price <= (entry_price - TAKE_PROFIT_PTS):
                            print("Closing Short Position...")
                            place_order("buy")
                            in_position = False
                            position_side = None

                # Entry Conditions
                elif not in_position:
                    # Long Entry Condition
                    if (prev_price < fib_05 <= current_price) and (current_price > ema_4h) and (rsi > 48) and (adx > 20):
                        print(">>> LONG Entry Triggered <<<")
                        if place_order("buy"):
                            in_position = True
                            position_side = "buy"
                            entry_price = current_price
                    
                    # Short Entry Condition
                    elif (prev_price > fib_05 >= current_price) and (current_price < ema_4h) and (rsi < 52) and (adx > 20):
                        print(">>> SHORT Entry Triggered <<<")
                        if place_order("sell"):
                            in_position = True
                            position_side = "sell"
                            entry_price = current_price

        except Exception as e:
            print(f"Strategy Loop Error: {e}")
            
        time.sleep(60)

# ==========================================
# 5. BOT EXECUTION
# ==========================================
if __name__ == '__main__':
    t = Thread(target=strategy_loop)
    t.start()
    run_flask()
