"""
scanner.py — Elliott Wave 3 Signal Scanner
==========================================
Runs on GitHub Actions every 5 minutes.
Scans: XAUUSD, XAGUSD, AUDJPY, CADJPY
Sends Telegram alert on BUY/SELL Wave 3 signal.
"""

import os
import json
import time
import requests
import numpy as np
from datetime import datetime, timezone

# ═══════════════════════════════════
# CONFIG
# ═══════════════════════════════════
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT  = os.environ["TELEGRAM_CHAT"]

SYMBOLS = [
    {"ticker": "GC=F",    "name": "🥇 זהב XAU/USD"},
    {"ticker": "SI=F",    "name": "🥈 כסף XAG/USD"},
    {"ticker": "AUDJPY=X","name": "🇦🇺 AUD/JPY"},
    {"ticker": "CADJPY=X","name": "🇨🇦 CAD/JPY"},
]

STATE_FILE = "last_signals.json"   # tracks sent alerts to avoid duplicates

# ═══════════════════════════════════
# LOAD / SAVE STATE
# ═══════════════════════════════════
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ═══════════════════════════════════
# FETCH CANDLES
# ═══════════════════════════════════
def fetch_candles(ticker, interval="5m", period="2d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": interval, "range": period}
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            chart = data["chart"]["result"][0]
            ts     = chart["timestamp"]
            q      = chart["indicators"]["quote"][0]

            candles = []
            for i in range(len(ts)):
                if q["open"][i] is None or q["close"][i] is None:
                    continue
                candles.append({
                    "t": ts[i] * 1000,
                    "o": q["open"][i],
                    "h": q["high"][i],
                    "l": q["low"][i],
                    "c": q["close"][i],
                })
            return candles[-200:]   # last 200 bars
        except Exception as e:
            print(f"  Attempt {attempt+1} failed for {ticker}: {e}")
            time.sleep(2)

    return []

# ═══════════════════════════════════
# INDICATORS
# ═══════════════════════════════════
def sma(arr, period):
    result = [None] * len(arr)
    for i in range(period - 1, len(arr)):
        vals = [v for v in arr[i-period+1:i+1] if v is not None]
        if len(vals) == period:
            result[i] = sum(vals) / period
    return result

def ema(arr, period):
    result = [None] * len(arr)
    k = 2 / (period + 1)
    for i, v in enumerate(arr):
        if v is None:
            continue
        if result[i-1] is None:
            result[i] = v
        else:
            result[i] = v * k + result[i-1] * (1 - k)
    return result

def macd(closes, fast=10, slow=20, signal=9):
    ema_fast   = ema(closes, fast)
    ema_slow   = ema(closes, slow)
    macd_line  = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def stochastic(candles, period=10, smooth=3):
    raw = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        sl = candles[i-period+1:i+1]
        lo = min(c["l"] for c in sl)
        hi = max(c["h"] for c in sl)
        rng = hi - lo
        raw[i] = ((candles[i]["c"] - lo) / rng * 100) if rng > 0 else 50
    return sma(raw, smooth)

def smc_status(candles, period=15):
    result = []
    for i in range(len(candles)):
        if i < period:
            result.append("Range")
            continue
        sl  = candles[i-period:i]
        hi  = max(c["h"] for c in sl)
        lo  = min(c["l"] for c in sl)
        if candles[i]["c"] > hi:
            result.append("Bullish")
        elif candles[i]["c"] < lo:
            result.append("Bearish")
        else:
            result.append("Range")
    return result

def elliott_wave(candles, pivot_len=5):
    """Count Elliott waves 1-5 based on pivot highs/lows."""
    n      = len(candles)
    result = [0] * n
    w_cnt  = 0

    for i in range(pivot_len, n - pivot_len):
        highs = [c["h"] for c in candles]
        lows  = [c["l"] for c in candles]

        is_high = (
            all(highs[j] <= highs[i] for j in range(i-pivot_len, i)) and
            all(highs[j] <= highs[i] for j in range(i+1, i+pivot_len+1))
        )
        is_low = (
            all(lows[j] >= lows[i] for j in range(i-pivot_len, i)) and
            all(lows[j] >= lows[i] for j in range(i+1, i+pivot_len+1))
        )

        if is_high or is_low:
            w_cnt = (w_cnt % 5) + 1
            result[i] = w_cnt

    return result

# ═══════════════════════════════════
# SIGNAL DETECTION
# ═══════════════════════════════════
def detect_signal(candles):
    closes = [c["c"] for c in candles]

    ma2   = ema(closes, 20)
    ma3   = sma(closes, 80)
    ml, sl = macd(closes, 10, 20, 9)
    stoch = stochastic(candles, 10)
    smc   = smc_status(candles, 15)
    waves = elliott_wave(candles, 5)

    signals = []
    n = len(candles)

    for i in range(1, n):
        if any(v is None for v in [ma2[i], ma3[i], ml[i], sl[i], stoch[i]]):
            continue
        if any(v is None for v in [ma2[i-1], ma3[i-1], ml[i-1], sl[i-1]]):
            continue

        # BUY
        buy_now  = ma2[i] > ma3[i] and ml[i] > sl[i] and stoch[i] > 50 and smc[i] == "Bullish"
        buy_prev = ma2[i-1] > ma3[i-1] and ml[i-1] > sl[i-1] and stoch[i-1] > 50 and smc[i-1] == "Bullish"
        new_buy  = buy_now and not buy_prev

        # SELL
        sell_now  = ma2[i] < ma3[i] and ml[i] < sl[i] and stoch[i] < 50 and smc[i] == "Bearish"
        sell_prev = ma2[i-1] < ma3[i-1] and ml[i-1] < sl[i-1] and stoch[i-1] < 50 and smc[i-1] == "Bearish"
        new_sell  = sell_now and not sell_prev

        if waves[i] == 3:
            if new_buy:
                signals.append({"type": "BUY",  "wave": 3, "price": candles[i]["c"], "time": candles[i]["t"]})
            if new_sell:
                signals.append({"type": "SELL", "wave": 3, "price": candles[i]["c"], "time": candles[i]["t"]})

    return signals[-1] if signals else None

# ═══════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        print("  ✅ Telegram sent")
    except Exception as e:
        print(f"  ❌ Telegram error: {e}")

# ═══════════════════════════════════
# MAIN
# ═══════════════════════════════════
def main():
    print(f"\n{'='*50}")
    print(f"Scanner run: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}")

    state = load_state()

    for sym in SYMBOLS:
        ticker = sym["ticker"]
        name   = sym["name"]
        print(f"\n📊 Scanning {name}...")

        candles = fetch_candles(ticker)
        if len(candles) < 50:
            print(f"  ⚠️ Not enough data ({len(candles)} candles)")
            continue

        signal = detect_signal(candles)

        if signal:
            # Avoid duplicate alerts — check if same signal time was already sent
            sig_key = f"{ticker}_{signal['time']}"
            if state.get(sig_key):
                print(f"  ℹ️ Signal already sent: {sig_key}")
                continue

            # Only send if signal is fresh (within last 10 minutes)
            age_min = (time.time() * 1000 - signal["time"]) / 60000
            if age_min > 10:
                print(f"  ℹ️ Signal too old: {age_min:.1f} min ago")
                continue

            emoji  = "🟢" if signal["type"] == "BUY" else "🔴"
            action = "קנייה ✅" if signal["type"] == "BUY" else "מכירה ❌"
            cond   = (
                "EMA20 > SMA80 | MACD חיובי | SMC Bullish"
                if signal["type"] == "BUY"
                else "EMA20 < SMA80 | MACD שלילי | SMC Bearish"
            )

            msg = (
                f"{emoji} *{signal['type']} — גל אליוט 3*\n"
                f"\n"
                f"📊 *נכס:* {name}\n"
                f"💰 *מחיר:* {signal['price']:.4f}\n"
                f"🌊 *גל:* W{signal['wave']}\n"
                f"📈 *כיוון:* {action}\n"
                f"🕐 *זמן:* {datetime.fromtimestamp(signal['time']/1000, tz=timezone.utc).strftime('%H:%M UTC')}\n"
                f"\n"
                f"✅ _{cond}_"
            )

            print(f"  🚨 Signal: {signal['type']} W{signal['wave']} @ {signal['price']:.4f}")
            send_telegram(msg)

            # Save to state so we don't resend
            state[sig_key] = True

        else:
            print(f"  ✓ No Wave 3 signal")

        time.sleep(1)

    save_state(state)
    print(f"\n✅ Scan complete")

if __name__ == "__main__":
    main()
