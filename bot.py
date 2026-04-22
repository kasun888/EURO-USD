"""
OANDA — EUR/USD NY Session Scalp Bot  (Strategy V7-PLUS)
=========================================================
Pair:     EUR/USD only
Size:     74,000 units
SL:       7 pips   ≈ SGD 70
TP:       10 pips  ≈ SGD 100  [R:R 1.43]
Max dur:  45 minutes

SESSION: NY ONLY
  13:00–16:00 UTC  =  21:00–00:00 SGT
  Best EUR/USD window: US data releases, USD flows, cleanest trends.

SIGNAL (4 layers):
  L0  H4 EMA50       + last 3 bars same side (trend consistency)
  L1  H4 ATR(14)     > 6 pip (trending market)
  L2  H1 EMA20+EMA50 price above/below BOTH + ATR > 4.5p
  L3  M15 EMA9/EMA21 ongoing trend + RSI 38–62 + ATR > 4.5p
  L4  M5 close vs EMA9 + body ≥45%

RULES:
  - Max 1 trade per day
  - 15 min cooldown after any loss
  - CIRCUIT BREAKER: 2 SL hits in a row → pause 2 calendar days
    (Protects against trend reversal whipsaw periods)
  - 45 min hard close
  - News filter: skip 30 min before/after high-impact EUR/USD events
  - No trades Friday after 14:00 UTC
  - All Telegram alerts in SGD with live balance
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytz

from signals         import SignalEngine
from oanda_trader    import OandaTrader
from telegram_alert  import TelegramAlert
from calendar_filter import EconomicCalendar as CalendarFilter

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

sg_tz  = pytz.timezone("Asia/Singapore")
utc_tz = pytz.UTC
signals = SignalEngine()

# ── TRADE PARAMETERS ─────────────────────────────────────────────────
TRADE_SIZE    = 74_000
SL_PIPS       = 7        # V7-PLUS: tighter SL
TP_PIPS       = 10       # V7-PLUS: realistic for 45min NY window
MAX_DURATION  = 45
MAX_PER_DAY   = 1        # 1 quality trade per day
COOLDOWN_MIN  = 15
USD_SGD       = 1.35

# ── CIRCUIT BREAKER ───────────────────────────────────────────────────
# After 2 consecutive SL hits → pause for 2 days
# Protects against whipsaw/trend reversal periods
MAX_CONSEC_SL    = 2
PAUSE_DAYS       = 2

# ── SESSION ───────────────────────────────────────────────────────────
SESSION = {
    "label":      "NY",
    "utc_start":  13,
    "utc_end":    16,
    "sgt_label":  "21:00–00:00 SGT",
    "max_spread": 1.5,
}

ASSET = {
    "instrument": "EUR_USD",
    "asset":      "EURUSD",
    "emoji":      "🇪🇺",
    "pip":        0.0001,
    "precision":  5,
}

DEFAULT_SETTINGS = {"signal_threshold": 4, "demo_mode": True}
_SETTINGS_PATH   = Path(__file__).parent / "settings.json"


def load_settings():
    try:
        with open(_SETTINGS_PATH) as f:
            DEFAULT_SETTINGS.update(json.load(f))
    except FileNotFoundError:
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)
    return DEFAULT_SETTINGS


def is_in_session():
    now_utc = datetime.now(utc_tz)
    h = now_utc.hour
    # No trades Friday after 14:00 UTC
    if now_utc.weekday() == 4 and h >= 14:
        return False
    return SESSION["utc_start"] <= h < SESSION["utc_end"]


def set_cooldown(state):
    state["cooldown_until"] = datetime.now(timezone.utc).isoformat()
    log.info("Cooldown set — " + str(COOLDOWN_MIN) + " min")


def in_cooldown(state):
    cd = state.get("cooldown_until")
    if not cd:
        return False
    try:
        elapsed = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cd)).total_seconds() / 60
        return elapsed < COOLDOWN_MIN
    except Exception:
        return False


def cooldown_remaining(state):
    cd = state.get("cooldown_until")
    if not cd:
        return 0
    try:
        elapsed = (datetime.now(timezone.utc) -
                   datetime.fromisoformat(cd)).total_seconds() / 60
        return max(0, int(COOLDOWN_MIN - elapsed))
    except Exception:
        return "?"


def is_paused(state):
    """Circuit breaker — paused after 2 consecutive SL hits."""
    p = state.get("pause_until")
    if not p:
        return False
    try:
        return datetime.now(timezone.utc) < datetime.fromisoformat(p)
    except Exception:
        return False


def pause_remaining_days(state):
    p = state.get("pause_until")
    if not p:
        return 0
    try:
        remaining = (datetime.fromisoformat(p) -
                     datetime.now(timezone.utc)).total_seconds() / 86400
        return max(0, round(remaining, 1))
    except Exception:
        return "?"


def detect_sl_tp_hits(state, trader, alert):
    name = ASSET["instrument"]
    if name not in state.get("open_times", {}):
        return
    if trader.get_position(name):
        return

    try:
        url  = (trader.base_url + "/v3/accounts/" + trader.account_id +
                "/trades?state=CLOSED&instrument=" + name + "&count=1")
        data = requests.get(url, headers=trader.headers,
                            timeout=10).json().get("trades", [])
        if not data:
            return

        pnl        = float(data[0].get("realizedPL", "0"))
        pnl_sgd    = round(pnl * USD_SGD, 2)
        wins       = state.get("wins", 0)
        losses     = state.get("losses", 0)
        live_bal   = trader.get_balance()
        bal_sgd    = round(live_bal * USD_SGD, 2)

        if pnl < 0:
            set_cooldown(state)
            state["losses"]       = losses + 1
            consec = state.get("consec_sl", 0) + 1
            state["consec_sl"]    = consec
            state["consec_losses"] = state.get("consec_losses", 0) + 1

            # Circuit breaker
            cb_msg = ""
            if consec >= MAX_CONSEC_SL:
                pause_dt = datetime.now(timezone.utc) + timedelta(days=PAUSE_DAYS)
                state["pause_until"] = pause_dt.isoformat()
                state["consec_sl"]   = 0
                cb_msg = ("\n⛔ CIRCUIT BREAKER — " + str(MAX_CONSEC_SL) +
                          " SL in a row!\nPausing " + str(PAUSE_DAYS) +
                          " days to let market settle.")
                log.warning("Circuit breaker triggered — pausing " +
                            str(PAUSE_DAYS) + " days")

            alert.send(
                "🔴 SL HIT — LOSS\n"
                + ASSET["emoji"] + " EUR/USD\n"
                "Loss:      SGD -" + str(abs(pnl_sgd)) + "\n"
                "Balance:   SGD " + str(bal_sgd) + "\n"
                "⏳ Cooldown " + str(COOLDOWN_MIN) + " min\n"
                "W/L today: " + str(wins) + "/" + str(state["losses"]) +
                cb_msg
            )
        else:
            state["wins"]          = wins + 1
            state["consec_sl"]     = 0
            state["consec_losses"] = 0
            alert.send(
                "✅ TP HIT — WIN\n"
                + ASSET["emoji"] + " EUR/USD\n"
                "Profit:    SGD +" + str(pnl_sgd) + "\n"
                "Balance:   SGD " + str(bal_sgd) + "\n"
                "W/L today: " + str(state["wins"]) + "/" + str(losses)
            )
    except Exception as e:
        log.warning("SL/TP detect error: " + str(e))

    state.get("open_times", {}).pop(name, None)


def run_bot(state):
    settings = load_settings()
    now_utc  = datetime.now(utc_tz)
    now_sg   = datetime.now(sg_tz)
    today    = now_sg.strftime("%Y%m%d")
    alert    = TelegramAlert()
    calendar = CalendarFilter()

    log.info("Scan at " + now_sg.strftime("%H:%M:%S SGT") +
             "  (" + now_utc.strftime("%H:%M UTC") + ")")

    # ── Session gate ─────────────────────────────────────────────────
    if not is_in_session():
        log.info("Outside NY session (13–16 UTC / 21–00 SGT) — sleeping")
        return

    log.info("Session: NY (13:00–16:00 UTC / 21:00–00:00 SGT) | " +
             "Max spread: " + str(SESSION["max_spread"]) + "p")

    # ── Login ─────────────────────────────────────────────────────────
    trader = OandaTrader(demo=settings["demo_mode"])
    if not trader.login():
        log.warning("Login failed — skipping scan")
        return

    current_balance = trader.get_balance()
    if "start_balance" not in state or state["start_balance"] == 0.0:
        state["start_balance"] = current_balance

    # ── Session open alert (once per day, after live balance) ─────────
    alert_key = "ny_open_" + today
    if not state.get("session_alerted", {}).get(alert_key) and \
       now_utc.hour == SESSION["utc_start"]:
        state.setdefault("session_alerted", {})[alert_key] = True
        bal_sgd    = round(current_balance * USD_SGD, 2)
        start_sgd  = round(state.get("start_balance", current_balance) * USD_SGD, 2)
        daily_usd  = round(current_balance - state.get("start_balance", current_balance), 2)
        daily_sgd  = round(daily_usd * USD_SGD, 2)
        daily_sign = "+" if daily_sgd >= 0 else ""
        wins       = state.get("wins", 0)
        losses     = state.get("losses", 0)
        pause_info = ""
        if is_paused(state):
            pause_info = ("\n⛔ Circuit breaker active — " +
                          str(pause_remaining_days(state)) + " days remaining")
        alert.send(
            "🔔 NY Session Open!\n"
            "⏰ 21:00–00:00 SGT\n"
            "─────────────────\n"
            "💰 Balance:    SGD " + str(bal_sgd) + "\n"
            "📈 Day start:  SGD " + str(start_sgd) + "\n"
            "📊 Daily P&L:  SGD " + daily_sign + str(daily_sgd) + "\n"
            "🏆 W/L today:  " + str(wins) + "/" + str(losses) + "\n"
            "─────────────────\n"
            "TP=" + str(TP_PIPS) + "p (≈SGD " +
            str(round(TRADE_SIZE * TP_PIPS * ASSET["pip"] * USD_SGD, 0)) +
            ") | SL=" + str(SL_PIPS) + "p (≈SGD " +
            str(round(TRADE_SIZE * SL_PIPS * ASSET["pip"] * USD_SGD, 0)) +
            ")" + pause_info
        )

    detect_sl_tp_hits(state, trader, alert)

    name = ASSET["instrument"]

    # ── 45-min hard close ────────────────────────────────────────────
    pos = trader.get_position(name)
    if pos:
        try:
            trade_id, open_str = trader.get_open_trade_id(name)
            if trade_id and open_str:
                open_utc = datetime.fromisoformat(
                    open_str.replace("Z", "+00:00"))
                mins = (datetime.now(pytz.utc) -
                        open_utc).total_seconds() / 60
                log.info(name + ": open " + str(round(mins, 1)) + " min")
                if mins >= MAX_DURATION:
                    pnl     = trader.check_pnl(pos)
                    pnl_sgd = round(pnl * USD_SGD, 2)
                    trader.close_position(name)
                    state.get("open_times", {}).pop(name, None)
                    if pnl < 0:
                        set_cooldown(state)
                    live_bal_sgd = round(trader.get_balance() * USD_SGD, 2)
                    alert.send(
                        "⏰ 45-MIN TIMEOUT\n"
                        + ASSET["emoji"] + " EUR/USD\n"
                        "Closed at " + str(round(mins, 1)) + " min\n"
                        "PnL:     SGD " + ("+" if pnl_sgd >= 0 else "") +
                        str(pnl_sgd) + " " + ("✅" if pnl >= 0 else "🔴") + "\n"
                        "Balance: SGD " + str(live_bal_sgd)
                    )
        except Exception as e:
            log.warning("Duration check error: " + str(e))
        return

    # ── Daily trade limit ────────────────────────────────────────────
    today_trades = state.get("daily_trades", {}).get(today, 0)
    if today_trades >= MAX_PER_DAY:
        log.info("Daily limit reached (" + str(MAX_PER_DAY) + " trade) — done for today")
        return

    # ── Circuit breaker check ─────────────────────────────────────────
    if is_paused(state):
        log.info("Circuit breaker active — " +
                 str(pause_remaining_days(state)) + " days remaining. No trades.")
        return

    # ── Cooldown ─────────────────────────────────────────────────────
    if in_cooldown(state):
        log.info("Cooldown — " + str(cooldown_remaining(state)) + " min left")
        return

    # ── Price & spread ────────────────────────────────────────────────
    price, bid, ask = trader.get_price(name)
    if price is None:
        log.warning("Cannot get price — skipping")
        return

    spread_pip = (ask - bid) / ASSET["pip"]
    if spread_pip > SESSION["max_spread"] + 0.05:
        log.info("Spread " + str(round(spread_pip, 2)) + "p > max " +
                 str(SESSION["max_spread"]) + "p — skip")
        return

    # ── News filter ──────────────────────────────────────────────────
    news_active, news_reason = calendar.is_news_time(name)
    if news_active:
        news_key = name + "_news_" + now_sg.strftime("%Y%m%d%H")
        if not state.get("news_alerted", {}).get(news_key):
            state.setdefault("news_alerted", {})[news_key] = True
            alert.send("⚠️ NEWS BLOCK\n" + ASSET["emoji"] +
                       " EUR/USD\n" + news_reason + "\nSkipping trade")
        log.info("News block: " + news_reason)
        return

    # ── Signal scan ──────────────────────────────────────────────────
    threshold = settings.get("signal_threshold", 4)
    score, direction, details = signals.analyze(
        asset=ASSET["asset"], state=state)
    log.info(name + ": score=" + str(score) + "/" + str(threshold) +
             " dir=" + direction + " | " + details)

    if score < threshold or direction == "NONE":
        log.info(name + ": no setup — waiting for alignment")
        return

    # ── Place trade ──────────────────────────────────────────────────
    sl_sgd = round(TRADE_SIZE * SL_PIPS * ASSET["pip"] * USD_SGD, 2)
    tp_sgd = round(TRADE_SIZE * TP_PIPS * ASSET["pip"] * USD_SGD, 2)

    result = trader.place_order(
        instrument=name,
        direction=direction,
        size=TRADE_SIZE,
        stop_distance=SL_PIPS,
        limit_distance=TP_PIPS,
    )

    if result["success"]:
        state["trades"] = state.get("trades", 0) + 1
        state.setdefault("daily_trades", {})[today] = today_trades + 1
        state.setdefault("open_times", {})[name] = now_sg.isoformat()

        price, _, _ = trader.get_price(name)
        cur_bal_sgd = round(current_balance * USD_SGD, 2)
        alert.send(
            "🔄 NEW TRADE!  [NY Session]\n"
            + ASSET["emoji"] + " EUR/USD\n"
            "Direction: " + direction + "\n"
            "Entry:     " + str(round(price, ASSET["precision"])) + "\n"
            "─────────────────\n"
            "SL:        " + str(SL_PIPS) + " pips = SGD " + str(sl_sgd) + "\n"
            "TP:        " + str(TP_PIPS) + " pips = SGD " + str(tp_sgd) + "\n"
            "─────────────────\n"
            "Balance:   SGD " + str(cur_bal_sgd) + "\n"
            "Spread:    " + str(round(spread_pip, 2)) + "p | Score: " +
            str(score) + "/4 ✅\n"
            "Max hold:  45 min"
        )
        log.info(name + ": PLACED " + direction +
                 " TP=SGD" + str(tp_sgd) + " SL=SGD" + str(sl_sgd))
    else:
        set_cooldown(state)
        log.warning(name + ": order failed — " + str(result.get("error", "")))

    log.info("Scan complete.")
