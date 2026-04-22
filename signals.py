"""
Signal Engine — Strategy V7-PLUS "NY Trend Scalp"
==================================================
Pair:     EUR/USD ONLY
Session:  NY 13:00–16:00 UTC  (21:00–00:00 SGT)

TP:  10 pips  |  SL: 7 pips  |  R:R: 1.43
Max hold: 45 min

SIGNAL LOGIC (4 layers):
  L0  H4  EMA50      → macro direction (BUY/SELL)
               + last 3 H4 bars must be on same side (trend consistency)
  L1  H4  ATR(14)    → >6 pips (trending market, not flat)
  L2  H1  EMA20+EMA50→ price above BOTH EMAs for buy / below for sell
       H1  ATR(14)   → >4.5 pips (session active)
  L3  M15 EMA9/EMA21 → EMA9 above EMA21 (bull) or below (bear)
       M15 RSI(14)   → 38–62 (momentum zone)
       M15 ATR(14)   → >4.5 pips
  L4  M5  EMA9       → close above (buy) / below (sell) EMA9
       M5  body      → ≥45% of candle range in direction

WHY V7-PLUS OVER V4/V6:
  - H4 3-bar consistency catches trend reversals early (stopped March SL streak)
  - H1 dual EMA (both EMA20 + EMA50) = stronger trend confirmation
  - M15 RSI 38-62 = cleaner momentum entries, less overextended
  - TP=10 pips hits more frequently in 45min NY window vs old 12/26pip
  - Circuit breaker in bot.py prevents 6+ consecutive SL streak
"""

import os
import logging
import requests

log = logging.getLogger(__name__)


class SafeFilter(logging.Filter):
    def __init__(self):
        self.api_key = os.environ.get("OANDA_API_KEY", "")

    def filter(self, record):
        if self.api_key and self.api_key in str(record.getMessage()):
            record.msg = record.msg.replace(self.api_key, "***")
        return True


log.addFilter(SafeFilter())


class SignalEngine:
    def __init__(self):
        self.api_key  = os.environ.get("OANDA_API_KEY", "")
        self.base_url = "https://api-fxpractice.oanda.com"
        self.headers  = {"Authorization": "Bearer " + self.api_key}

    # ─────────────────────────────────────────────────────────────────
    # DATA FETCHING
    # ─────────────────────────────────────────────────────────────────

    def _fetch_candles(self, instrument, granularity, count=60):
        url    = self.base_url + "/v3/instruments/" + instrument + "/candles"
        params = {"count": str(count), "granularity": granularity, "price": "M"}
        for attempt in range(3):
            try:
                r = requests.get(url, headers=self.headers,
                                 params=params, timeout=10)
                if r.status_code == 200:
                    c = [x for x in r.json()["candles"] if x["complete"]]
                    return (
                        [float(x["mid"]["c"]) for x in c],
                        [float(x["mid"]["h"]) for x in c],
                        [float(x["mid"]["l"]) for x in c],
                        [float(x["mid"]["o"]) for x in c],
                    )
                log.warning("Candle " + granularity + " attempt " +
                            str(attempt + 1) + " HTTP " + str(r.status_code))
            except Exception as e:
                log.warning("Candle fetch error: " + str(e))
        return [], [], [], []

    # ─────────────────────────────────────────────────────────────────
    # INDICATORS
    # ─────────────────────────────────────────────────────────────────

    def _ema(self, data, period):
        if not data:
            return [0.0]
        if len(data) < period:
            return [sum(data) / len(data)] * len(data)
        seed = sum(data[:period]) / period
        emas = [seed] * period
        mult = 2 / (period + 1)
        for p in data[period:]:
            emas.append((p - emas[-1]) * mult + emas[-1])
        return emas

    def _rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(max(delta, 0))
            losses.append(max(-delta, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    def _atr(self, highs, lows, closes, period=14):
        if len(highs) < period + 1:
            return 0.0
        trs = []
        for i in range(1, len(highs)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i]  - closes[i - 1]),
            )
            trs.append(tr)
        return sum(trs[-period:]) / period

    # ─────────────────────────────────────────────────────────────────
    # MAIN SIGNAL
    # ─────────────────────────────────────────────────────────────────

    def analyze(self, asset="EURUSD", state=None):
        """
        Returns (score, direction, reason_string).
        score=4 + direction != "NONE"  →  fire trade.
        Session gating is handled by bot.py.
        """
        return self._v7_signal("EUR_USD")

    def _v7_signal(self, instrument):
        reasons = []

        # ── L0: H4 direction + 3-bar trend consistency ────────────────
        h4_c, h4_h, h4_l, _ = self._fetch_candles(instrument, "H4", 60)
        if len(h4_c) < 54:
            return 0, "NONE", "Not enough H4 data (" + str(len(h4_c)) + ")"

        h4_ema50 = self._ema(h4_c, 50)
        # Current EMA50 value
        ema50_now = h4_ema50[-1]

        # Last 3 H4 closes must all be on same side of EMA50
        last3_closes = h4_c[-3:]
        last3_ema    = h4_ema50[-3:]
        bull_h4 = all(c > e for c, e in zip(last3_closes, last3_ema))
        bear_h4 = all(c < e for c, e in zip(last3_closes, last3_ema))

        if not bull_h4 and not bear_h4:
            return 0, "NONE", "H4 trend not consistent (last 3 bars mixed around EMA50)"

        direction = "BUY" if bull_h4 else "SELL"
        reasons.append("✅ L0 H4 " + direction + " — 3 bars consistent above/below EMA50=" +
                       str(round(ema50_now, 5)))

        # ── L1: H4 ATR > 6 pip ───────────────────────────────────────
        h4_atr_pip = self._atr(h4_h, h4_l, h4_c, 14) / 0.0001
        if h4_atr_pip < 6.0:
            msg = ("🚫 L1 FAIL — H4 ATR=" + str(round(h4_atr_pip, 1)) +
                   "p < 6.0p (choppy/flat market)")
            log.info(instrument + ": " + msg)
            return 1, "NONE", " | ".join(reasons) + " | " + msg
        reasons.append("✅ L1 H4 ATR=" + str(round(h4_atr_pip, 1)) + "p — trending")

        # ── L2: H1 price above/below BOTH EMA20 and EMA50 ────────────
        h1_c, h1_h, h1_l, _ = self._fetch_candles(instrument, "H1", 60)
        if len(h1_c) < 51:
            return 1, "NONE", " | ".join(reasons) + " | Not enough H1 data"

        h1_ema20   = self._ema(h1_c, 20)[-1]
        h1_ema50   = self._ema(h1_c, 50)[-1]
        h1_atr_pip = self._atr(h1_h, h1_l, h1_c, 14) / 0.0001
        h1_price   = h1_c[-1]

        if h1_atr_pip < 4.5:
            msg = ("🚫 L2 FAIL — H1 ATR=" + str(round(h1_atr_pip, 1)) +
                   "p < 4.5p (session quiet)")
            return 1, "NONE", " | ".join(reasons) + " | " + msg

        bull_h1 = (h1_price > h1_ema20) and (h1_price > h1_ema50)
        bear_h1 = (h1_price < h1_ema20) and (h1_price < h1_ema50)

        if direction == "BUY" and not bull_h1:
            msg = ("L2 FAIL — H1 not above both EMAs: price=" +
                   str(round(h1_price, 5)) +
                   " EMA20=" + str(round(h1_ema20, 5)) +
                   " EMA50=" + str(round(h1_ema50, 5)))
            return 1, "NONE", " | ".join(reasons) + " | " + msg
        if direction == "SELL" and not bear_h1:
            msg = ("L2 FAIL — H1 not below both EMAs: price=" +
                   str(round(h1_price, 5)) +
                   " EMA20=" + str(round(h1_ema20, 5)) +
                   " EMA50=" + str(round(h1_ema50, 5)))
            return 1, "NONE", " | ".join(reasons) + " | " + msg

        reasons.append("✅ L2 H1 aligned — price " +
                       ("above" if direction == "BUY" else "below") +
                       " EMA20=" + str(round(h1_ema20, 5)) +
                       " EMA50=" + str(round(h1_ema50, 5)) +
                       " ATR=" + str(round(h1_atr_pip, 1)) + "p")

        # ── L3: M15 EMA9>EMA21 + RSI 38-62 + ATR>4.5 ────────────────
        m15_c, m15_h, m15_l, _ = self._fetch_candles(instrument, "M15", 30)
        if len(m15_c) < 22:
            return 2, "NONE", " | ".join(reasons) + " | Not enough M15 data"

        m15_ema9  = self._ema(m15_c, 9)[-1]
        m15_ema21 = self._ema(m15_c, 21)[-1]
        m15_rsi   = self._rsi(m15_c, 14)
        m15_atr_p = self._atr(m15_h, m15_l, m15_c, 14) / 0.0001

        if m15_atr_p < 4.5:
            msg = ("🚫 L3 FAIL — M15 ATR=" + str(round(m15_atr_p, 1)) + "p < 4.5p")
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        if not (38 < m15_rsi < 62):
            msg = ("🚫 L3 FAIL — M15 RSI=" + str(round(m15_rsi, 1)) +
                   " outside 38–62 (overextended)")
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        m15_bull = m15_ema9 > m15_ema21
        m15_bear = m15_ema9 < m15_ema21

        if direction == "BUY" and not m15_bull:
            msg = ("L3 FAIL — M15 EMA9=" + str(round(m15_ema9, 5)) +
                   " < EMA21=" + str(round(m15_ema21, 5)) + " (no bull stack)")
            return 2, "NONE", " | ".join(reasons) + " | " + msg
        if direction == "SELL" and not m15_bear:
            msg = ("L3 FAIL — M15 EMA9=" + str(round(m15_ema9, 5)) +
                   " > EMA21=" + str(round(m15_ema21, 5)) + " (no bear stack)")
            return 2, "NONE", " | ".join(reasons) + " | " + msg

        reasons.append("✅ L3 M15 EMA9=" + str(round(m15_ema9, 5)) +
                       (" > " if m15_bull else " < ") +
                       "EMA21=" + str(round(m15_ema21, 5)) +
                       " RSI=" + str(round(m15_rsi, 1)) +
                       " ATR=" + str(round(m15_atr_p, 1)) + "p")

        # ── L4: M5 close vs EMA9 + body ≥45% ─────────────────────────
        m5_c, m5_h, m5_l, m5_o = self._fetch_candles(instrument, "M5", 15)
        if len(m5_c) < 10:
            return 3, "NONE", " | ".join(reasons) + " | Not enough M5 data"

        m5_ema9    = self._ema(m5_c, 9)[-1]
        last_c     = m5_c[-1]
        last_o     = m5_o[-1]
        last_h     = m5_h[-1]
        last_l     = m5_l[-1]
        candle_rng = max(last_h - last_l, 0.00001)

        bull_body = (last_c > last_o) and ((last_c - last_l) / candle_rng >= 0.45)
        bear_body = (last_c < last_o) and ((last_h - last_c) / candle_rng >= 0.45)
        bull_ema9 = last_c > m5_ema9
        bear_ema9 = last_c < m5_ema9

        if direction == "BUY" and bull_body and bull_ema9:
            reasons.append("✅ L4 M5 BUY — close=" + str(round(last_c, 5)) +
                           " > EMA9=" + str(round(m5_ema9, 5)) +
                           " body=" + str(round((last_c - last_l) / candle_rng * 100)) + "%")
        elif direction == "SELL" and bear_body and bear_ema9:
            reasons.append("✅ L4 M5 SELL — close=" + str(round(last_c, 5)) +
                           " < EMA9=" + str(round(m5_ema9, 5)) +
                           " body=" + str(round((last_h - last_c) / candle_rng * 100)) + "%")
        else:
            msg = ("L4 FAIL — M5 EMA9=" + str(round(m5_ema9, 5)) +
                   " close=" + str(round(last_c, 5)) +
                   " bull_body=" + str(bull_body) +
                   " bear_body=" + str(bear_body) +
                   " side_ok=" + str(bull_ema9 if direction == "BUY" else bear_ema9))
            return 3, "NONE", " | ".join(reasons) + " | " + msg

        # ── ALL 4 LAYERS PASSED ───────────────────────────────────────
        log.info(instrument + ": ✅ ALL LAYERS PASSED — firing " + direction)
        return 4, direction, " | ".join(reasons)
