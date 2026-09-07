import os, hmac, hashlib, time, requests, json
from flask import Flask, request, jsonify

app = Flask(__name__)

API_KEY = "zemWuOQERwluGB0QmkgeFI1n4gnxC6"
API_SECRET = "rNmbnx4fOfsN6RYItSlJCtcehgJi3QfcDm0t13YRAci6rnoB0TF86XQfoco8"
BASE_URL = "https://cdn-ind.testnet.delta.exchange"

def generate_signature(method, endpoint, payload_str, timestamp):
    message = method + timestamp + endpoint + payload_str
    return hmac.new(API_SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

def send_delta_order(action, product_id=1, size=1):
    endpoint = "/v2/orders"
    timestamp = str(int(time.time()))
    payload = {
        "product_id": product_id,
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
    response = requests.post(BASE_URL + endpoint, data=payload_str, headers=headers)
    return response.json()

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json or {}
    action = data.get("action")
    if action in ["BUY", "SELL"]:
        result = send_delta_order(action=action)
        return jsonify({"status": "success", "delta_response": result}), 200
    return jsonify({"status": "ignored"}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
