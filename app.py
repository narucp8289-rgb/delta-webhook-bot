import os, time, hmac, hashlib, requests, json

API_KEY = "zemWuOQERwluGB0QmkgeFI1n4gnxC6"
API_SECRET = "rNmbnx4fOfsN6RYItSlJCtcehgJi3QfcDm0t13YRAci6rnoB0TF86XQfoco8"
BASE_URL = "https://cdn-ind.testnet.delta.exchange"
PRODUCT_ID = 1  # ETH/USD Product ID

position = None  # Current position state: 'BUY', 'SELL', or None

def generate_signature(method, endpoint, payload_str, timestamp):
    message = method + timestamp + endpoint + payload_str
    return hmac.new(API_SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

def send_order(action, size=1):
    global position
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
        print(f"Order Executed [{action}]:", res.json())
        position = action
    except Exception as e:
        print("Order Error:", e)

def fetch_candles():
    # Fetch recent 1-hour candles for ETH/USD
    url = f"{BASE_URL}/v2/chart/history?resolution=60&symbol=ETHUSD"
    try:
        res = requests.get(url).json()
        if res.get("success"):
            return res["result"]
    except Exception as e:
        print("Candle Fetch Error:", e)
    return None

def check_fibonacci_strategy():
    candles = fetch_candles()
    if not candles or len(candles) < 20:
        return

    # Extract High, Low, and Current Close prices
    highs = [c['h'] for c in candles[-20:]]
    lows = [c['l'] for c in candles[-20:]]
    current_close = candles[-1]['c']

    max_high = max(highs)
    min_low = min(lows)

    # 0.5 Fibonacci Level Calculation
    fib_0_5 = min_low + (max_high - min_low) * 0.5

    print(f"High: {max_high} | Low: {min_low} | Fib 0.5: {fib_0_5} | Current Price: {current_close}")

    # Trading Logic
    global position
    if current_close > fib_0_5 and position != "BUY":
        print(">>> Fibonacci 0.5 Bullish Crossover! Executing BUY...")
        send_order("BUY")
    elif current_close < fib_0_5 and position != "SELL":
        print(">>> Fibonacci 0.5 Bearish Crossover! Executing SELL...")
        send_order("SELL")

if __name__ == "__main__":
    print("Direct Delta Fibonacci Bot Started...")
    while True:
        check_fibonacci_strategy()
        time.sleep(60)  # Check strategy every 1 minute
