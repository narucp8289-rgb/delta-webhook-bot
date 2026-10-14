from datetime import datetime
import os
import threading
import time
from flask import Flask, jsonify
import numpy as np
import pandas as pd
import requests

app = Flask(__name__)

# ================= CONFIGURATION =================
OKX_URL = "https://www.okx.com/api/v5/market/candles"
SYMBOLS = ["PAXG-USDT", "ETH-USDT", "BTC-USDT", "SOL-USDT"]

TELEGRAM_TOKEN = "8682624980:AAEBi3mlG6dTnG0DOmq5nJ50HsSLjU0FrFo"
TELEGRAM_CHAT_ID = "5305261922"

# Gold Sweep Strategy Parameters
SL_DIST = 1.00  # 100 ticks = $1.00
TP_DIST = 2.00  # 200 ticks = $2.00

SYMBOL_CONFIG = {
    "PAXG-USDT": {
        "tag": "🟨 🪙 GOLD / PAXG 🟨",
        "buy_hdr": "🟩 BUY SIGNAL: PAXG-USDT 🟩",
        "sell_hdr": "🟥 SELL SIGNAL: PAXG-USDT 🟥",
    },
    "BTC-USDT": {
        "tag": "🟧 ₿ BITCOIN 🟧",
        "buy_hdr": "🟩 BUY SIGNAL: BTC-USDT 🟩",
        "sell_hdr": "🟥 SELL SIGNAL: BTC-USDT 🟥",
    },
    "ETH-USDT": {
        "tag": "🟦 🔷 ETHEREUM 🟦",
        "buy_hdr": "🟩 BUY SIGNAL: ETH-USDT 🟩",
        "sell_hdr": "🟥 SELL SIGNAL: ETH-USDT 🟥",
    },
    "SOL-USDT": {
        "tag": "🟪 🟣 SOLANA 🟪",
        "buy_hdr": "🟪🟩 BUY SIGNAL: SOL-USDT 🟪🟩",
        "sell_hdr": "🟪🟥 SELL SIGNAL: SOL-USDT 🟪🟥",
    },
}

latest_market_data = {}
last_error = "None"
active_signals = {symbol: None for symbol in SYMBOLS}
last_processed_candle_ts = {symbol: None for symbol in SYMBOLS}


def send_telegram(message):
  try:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    requests.post(url, json=payload, timeout=5)
  except Exception as e:
    print(f"Telegram Error: {e}")


def get_candles(symbol, bar="1H", limit=100):
  global last_error
  try:
    params = {"instId": symbol, "bar": bar, "limit": limit}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(OKX_URL, params=params, headers=headers, timeout=4)
    res = response.json()

    if res.get("code") == "0" and "data" in res:
      raw_data = res["data"]
      if len(raw_data) > 0:
        df = pd.DataFrame(
            raw_data,
            columns=[
                "ts",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "volCcy",
                "volCcyQuote",
                "confirm",
            ],
        )
        for col in ["close", "high", "low", "open", "volume"]:
          df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.iloc[::-1].reset_index(drop=True)
        df = df.dropna(subset=["close", "high", "low", "volume"]).reset_index(
            drop=True
        )
        return df
  except Exception as e:
    last_error = f"Fetch exception ({symbol} - {bar}): {e}"
  return None


def get_previous_day_high_low(symbol):
  """Daily High & Low મેળવવા માટેનું ફંક્શન."""
  df_daily = get_candles(symbol, bar="1D", limit=5)
  if df_daily is not None and len(df_daily) >= 2:
    prev_day = df_daily.iloc[-2]  # Yesterday's candle
    return float(prev_day["high"]), float(prev_day["low"])
  return None, None


def calculate_indicators(symbol):
  df_1h = get_candles(symbol, bar="1H", limit=50)
  if df_1h is None or len(df_1h) < 10:
    return None

  prev_day_high, prev_day_low = get_previous_day_high_low(symbol)
  if prev_day_high is None or prev_day_low is None:
    return None

  entry_candle = df_1h.iloc[-2]  # Completed candle

  candle_ts = str(entry_candle["ts"])
  entry_close = float(entry_candle["close"])
  entry_open = float(entry_candle["open"])
  entry_high = float(entry_candle["high"])
  entry_low = float(entry_candle["low"])

  is_red = entry_close < entry_open
  is_green = entry_close > entry_open

  # 📌 BUY Strategy: Low < Prev Day Low, Close > Prev Day Low, and Red Candle
  buy_signal = bool(
      (entry_low < prev_day_low) and (entry_close > prev_day_low) and is_red
  )

  # 📌 SELL Strategy: High > Prev Day High, Close < Prev Day High, and Green Candle
  sell_signal = bool(
      (entry_high > prev_day_high)
      and (entry_close < prev_day_high)
      and is_green
  )

  return {
      "symbol": symbol,
      "candle_ts": candle_ts,
      "price": round(entry_close, 2),
      "high": round(entry_high, 2),
      "low": round(entry_low, 2),
      "open": round(entry_open, 2),
      "prev_day_high": round(prev_day_high, 2),
      "prev_day_low": round(prev_day_low, 2),
      "buy_signal": buy_signal,
      "sell_signal": sell_signal,
  }


def alert_bot_loop():
  global latest_market_data, active_signals, last_processed_candle_ts
  while True:
    for symbol in SYMBOLS:
      try:
        data = calculate_indicators(symbol)
        if data:
          latest_market_data[symbol] = data
          current_candle_ts = data["candle_ts"]

          cfg = SYMBOL_CONFIG.get(
              symbol,
              {
                  "tag": symbol,
                  "buy_hdr": f"*BUY: {symbol}*",
                  "sell_hdr": f"*SELL: {symbol}*",
              },
          )

          curr_df = get_candles(symbol, bar="1m", limit=2)
          if curr_df is not None and len(curr_df) > 0:
            live_high = float(curr_df.iloc[-1]["high"])
            live_low = float(curr_df.iloc[-1]["low"])
          else:
            live_high, live_low = data["high"], data["low"]

          # 📌 Active Positions SL / TP Tracking (100 ticks SL / 200 ticks TP)
          if active_signals[symbol] is not None:
            act = active_signals[symbol]
            side = act["side"]
            sl = act["sl"]
            tp = act["tp"]
            entry = act["entry"]

            if side == "BUY":
              if live_high >= tp:
                send_telegram(
                    "🎯 *TAKE PROFIT HIT (+200 Ticks)!*\n"
                    f"{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}"
                )
                active_signals[symbol] = None
              elif live_low <= sl:
                send_telegram(
                    "🛑 *STOP LOSS HIT (-100 Ticks)!*\n"
                    f"{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}"
                )
                active_signals[symbol] = None

            elif side == "SELL":
              if live_low <= tp:
                send_telegram(
                    "🎯 *TAKE PROFIT HIT (+200 Ticks)!*\n"
                    f"{cfg['tag']}\n\n📌 Entry: ${entry}\n🎯 TP: ${tp}"
                )
                active_signals[symbol] = None
              elif live_high >= sl:
                send_telegram(
                    "🛑 *STOP LOSS HIT (-100 Ticks)!*\n"
                    f"{cfg['tag']}\n\n📌 Entry: ${entry}\n🛑 SL: ${sl}"
                )
                active_signals[symbol] = None

          # 📌 Signal Generation
          if (
              active_signals[symbol] is None
              and last_processed_candle_ts[symbol] != current_candle_ts
          ):

            # 1. SELL SIGNAL
            if data["sell_signal"]:
              entry_price = data["price"]
              sl = round(entry_price + SL_DIST, 2)  # Entry + $1.00
              tp = round(entry_price - TP_DIST, 2)  # Entry - $2.00

              active_signals[symbol] = {
                  "side": "SELL",
                  "sl": sl,
                  "tp": tp,
                  "entry": entry_price,
              }
              last_processed_candle_ts[symbol] = current_candle_ts

              msg = (
                  f"{cfg['tag']}\n"
                  f"{cfg['sell_hdr']}\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"📌 Entry Price: ${entry_price}\n"
                  f"🛑 Stop Loss: ${sl} (*100 Ticks / $1.00*)\n"
                  f"🎯 Take Profit: ${tp} (*200 Ticks / $2.00*)\n"
                  f"📈 Swept Prev High: ${data['prev_day_high']}\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"👉 Action: SELL / SHORT"
              )
              send_telegram(msg)

            # 2. BUY SIGNAL
            elif data["buy_signal"]:
              entry_price = data["price"]
              sl = round(entry_price - SL_DIST, 2)  # Entry - $1.00
              tp = round(entry_price + TP_DIST, 2)  # Entry + $2.00

              active_signals[symbol] = {
                  "side": "BUY",
                  "sl": sl,
                  "tp": tp,
                  "entry": entry_price,
              }
              last_processed_candle_ts[symbol] = current_candle_ts

              msg = (
                  f"{cfg['tag']}\n"
                  f"{cfg['buy_hdr']}\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"📌 Entry Price: ${entry_price}\n"
                  f"🛑 Stop Loss: ${sl} (*100 Ticks / $1.00*)\n"
                  f"🎯 Take Profit: ${tp} (*200 Ticks / $2.00*)\n"
                  f"📉 Swept Prev Low: ${data['prev_day_low']}\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"👉 Action: BUY / LONG"
              )
              send_telegram(msg)

      except Exception as e:
        print(f"Loop Error ({symbol}): {e}")
      time.sleep(0.1)
    time.sleep(0.3)


threading.Thread(target=alert_bot_loop, daemon=True).start()


@app.route("/")
def home():
  global latest_market_data, active_signals
  return jsonify({
      "status": "running",
      "mode": "Gold Sweep Strategy Bot (SL: $1.00, TP: $2.00)",
      "active_signals": active_signals,
      "market_data": latest_market_data,
  })


if __name__ == "__main__":
  send_telegram("⚡ *Gold Sweep Strategy Live Bot Active*")
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)
