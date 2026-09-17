import os
import time
import requests
import pandas as pd
import ccxt
from datetime import datetime

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")

SYMBOLS = ['ETH/USDT', 'BTC/USDT', 'SOL/USDT']
TIMEFRAME_PRIMARY = '3m'
TIMEFRAME_HTF_1 = '30m'
TIMEFRAME_HTF_2 = '1h'

# Minimum Stop Loss in USD per asset
MIN_SL_DICT = {
    'ETH/USDT': 15.0,
    'BTC/USDT': 200.0,
    'SOL/USDT': 1.0
}

# CCXT Exchange Setup
exchange = ccxt.okx({'enableRateLimit': True})

# Global Memory for Caching and Entry Tracking
htf_cache = {}
last_processed_candles = {sym: None for sym in SYMBOLS}

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def fetch_ohlcv_fast(symbol, timeframe, limit=100):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Error fetching {symbol} {timeframe}: {e}")
        return None

def calculate_ema(df, period):
    return df['close'].ewm(span=period, adjust=False).mean()

def get_cached_htf_trend(symbol):
    """Caches HTF trend to eliminate 1-candle API delay"""
    now = time.time()
    if symbol in htf_cache and (now - htf_cache[symbol]['time']) < 300:  # Cache for 5 mins
        return htf_cache[symbol]['trend']

    df_30m = fetch_ohlcv_fast(symbol, TIMEFRAME_HTF_1, limit=50)
    df_1h = fetch_ohlcv_fast(symbol, TIMEFRAME_HTF_2, limit=50)

    if df_30m is me or df_30m is None or df_1h is None:
        return 'NEUTRAL'

    ema_30m = calculate_ema(df_30m, 21).iloc[-1]
    ema_1h = calculate_ema(df_1h, 21).iloc[-1]

    close_30m = df_30m['close'].iloc[-1]
    close_1h = df_1h['close'].iloc[-1]

    if close_30m > ema_30m and close_1h > ema_1h:
        trend = 'BULLISH'
    elif close_30m < ema_30m and close_1h < ema_1h:
        trend = 'BEARISH'
    else:
        trend = 'NEUTRAL'

    htf_cache[symbol] = {'trend': trend, 'time': now}
    return trend

def run_bot_cycle():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking markets...")
    
    for symbol in SYMBOLS:
        df = fetch_ohlcv_fast(symbol, TIMEFRAME_PRIMARY, limit=30)
        if df is None or len(df) < 20:
            continue

        # Get closed candle
        closed_candle = df.iloc[-2]
        candle_time = closed_candle['timestamp']

        # Prevent duplicate or delayed execution
        if last_processed_candles[symbol] == candle_time:
            continue

        # Check breakout on closed candle
        swing_high = df['high'].iloc[-12:-2].max()
        swing_low = df['low'].iloc[-12:-2].min()
        close_price = closed_candle['close']

        htf_trend = get_cached_htf_trend(symbol)
        min_sl = MIN_SL_DICT.get(symbol, 10.0)

        # BUY SIGNAL
        if htf_trend == 'BULLISH' and close_price > swing_high:
            # Entry on exact breakout candle close
            entry = close_price
            sl_dist = max(min_sl, round(entry - swing_low, 2))
            sl = round(entry - sl_dist, 2)
            tp = round(entry + (sl_dist * 2.0), 2)

            msg = (f"🚀 *HIGH-ACCURACY BUY SIGNAL ({symbol.split('/')[0]}-USDT)*\n\n"
                   f"📌 *Entry Price:* ${entry}\n"
                   f"🛑 *Stop Loss:* ${sl}\n"
                   f"🎯 *Take Profit:* ${tp}\n"
                   f"📉 *Swing High Breakout:* ${round(swing_high, 2)}\n\n"
                   f"👉 *Action:* Delta Exchange માં BUY (LONG) કરો.")
            send_telegram_message(msg)
            last_processed_candles[symbol] = candle_time

        # SELL SIGNAL
        elif htf_trend == 'BEARISH' and close_price < swing_low:
            entry = close_price
            sl_dist = max(min_sl, round(swing_high - entry, 2))
            sl = round(entry + sl_dist, 2)
            tp = round(entry - (sl_dist * 2.0), 2)

            msg = (f"🔻 *HIGH-ACCURACY SELL SIGNAL ({symbol.split('/')[0]}-USDT)*\n\n"
                   f"📌 *Entry Price:* ${entry}\n"
                   f"🛑 *Stop Loss:* ${sl}\n"
                   f"🎯 *Take Profit:* ${tp}\n"
                   f"📈 *Swing Low Breakout:* ${round(swing_low, 2)}\n\n"
                   f"👉 *Action:* Delta Exchange માં SELL (SHORT) કરો.")
            send_telegram_message(msg)
            last_processed_candles[symbol] = candle_time

# Initial Telegram Message
send_telegram_message("⚡ *Upgraded Ultra-Fast Bot Active (Zero Delay Fix Applied)*")

# Main Execution Loop
while True:
    try:
        run_bot_cycle()
    except Exception as e:
        print(f"Loop Exception: {e}")
    time.sleep(5)  # Fast 5-second polling loop
