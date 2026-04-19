"""
OANDA — EUR/USD NY Session Scalp Bot  (Strategy V4)
=====================================================
Pair:     EUR/USD only
Size:     74,000 units
SL:       8 pips
TP:       12 pips   [R:R 1.5]
Max dur:  45 minutes

BACKTEST RESULTS (Jan 2 – Apr 18 2026, 107 days):
  45 trades | 53.3% win rate | +17.7 pips | PF 1.13

SESSION — NY ONLY:
  13:00–16:00 UTC  =  21:00–00:00 SGT
  WHY: London open historically gives 34% WR on EUR/USD due to
       spread widening + false breakouts at open. NY session gives
       54%+ WR — US data releases, USD flows, cleanest trends.

SIGNAL (4 layers, fires every 5-min scan when aligned):
  L0  H4 EMA50       macro direction
  L1  H4 ATR(14)     >6 pip — trending market only
  L2  H1 EMA20       price alignment + RSI 25–75 + ATR >4.5p
  L3  M15 EMA9>EMA21 ongoing trend stack + RSI 35–65 + ATR >4.5p
  L4  M5 close vs EMA9 + strong body ≥45%

RULES:
  - Max 2 trades per day
  - 15-min cooldown after any SL or TIMEOUT loss
  - 45-min hard close (no overnight)
  - News filter: skip 30 min before/after high-impact USD/EUR events
  - Spread cap: 1.5 pips
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

import pytz

from signals        import SignalEngine
from oanda_trader   import OandaTrader
from telegram_alert import TelegramAlert
from calendar_filter import EconomicCalendar as CalendarFilter

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

sg_tz   = pytz.timezone("Asia/Singapore")
utc_tz  = pytz.UTC
signals = SignalEngine()

# ── TRADE PARAMETERS ─────────────────────────────────────────────────
TRADE_SIZE    = 74_000
SL_PIPS       = 8       # V4: was 13
TP_PIPS       = 12      # V4: was 26 — tight enough to hit in session
MAX_DURATION  = 45      # V4: was 30 — extra 15 min to reach TP
MAX_PER_DAY   = 2       # max trades per calendar day
COOLDOWN_MIN  = 15      # minutes after SL/loss before next entry
USD_SGD       = 1.35

# ── ASSET CONFIG ─────────────────────────────────────────────────────
ASSET = {
    "instrument": "EUR_USD",
    "asset":      "EURUSD",
    "emoji":      "🇪🇺",
    "pip":        0.0001,
    "precision":  5,
    "stop_pips":  SL_PIPS,
    "tp_pips":    TP_PIPS,
    # NY session ONLY: 13:00–16:00 UTC = 21:00–00:00 SGT
    # Note: SGT = UTC+8, so 13 UTC = 21 SGT, 16 UTC = 00 SGT (next day)
    "session": {"utc_start": 13, "utc_end": 16,
                "label": "NY", "max_spread": 1.5},
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
    """True if current UTC hour is in the NY 13:00–16:00 window."""
    now_utc = datetime.now(utc_tz)
    h = now_utc.hour
    s = ASSET["session"]
    return s["utc_start"] <= h < s["utc_end"]


def get_session_label():
    return ASSET["session"]["label"] if is_in_session() else None


def set_cooldown(state):
    state["cooldown_until"] = datetime.now(timezone.utc).isoformat()
    log.info("Cooldown set — " + str(COOLDOWN_MIN) + " min before next entry")


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


def detect_sl_tp_hits(state, trader, alert):
    """Detect closed trades and update W/L counters."""
    name = ASSET["instrument"]
    if name not in state.get("open_times", {}):
        return
    if trader.get_position(name):
        return  # still open

    try:
        url  = (trader.base_url + "/v3/accounts/" + trader.account_id +
                "/trades?state=CLOSED&instrument=" + name + "&count=1")
        data = requests.get(url, headers=trader.headers, timeout=10
                            ).json().get("trades", [])
        if not data:
            return

        pnl     = float(data[0].get("realizedPL", "0"))
        pnl_sgd = round(pnl * USD_SGD, 2)
        wins    = state.get("wins", 0)
        losses  = state.get("losses", 0)
        emoji   = ASSET["emoji"]

        if pnl < 0:
            set_cooldown(state)
            state["losses"]        = losses + 1
            state["consec_losses"] = state.get("consec_losses", 0) + 1
            alert.send(
                "🔴 SL / LOSS CLOSED\n" + emoji + " EUR/USD\n"
                "Loss:  $" + str(round(pnl, 2)) + " USD\n"
                "     ≈ SGD -" + str(abs(pnl_sgd)) + "\n"
                "⏳ Cooldown " + str(COOLDOWN_MIN) + " min\n"
                "W/L: " + str(wins) + "/" + str(state["losses"])
            )
        else:
            state["wins"]          = wins + 1
            state["consec_losses"] = 0
            alert.send(
                "✅ TP HIT\n" + emoji + " EUR/USD\n"
                "Profit: $+" + str(round(pnl, 2)) + " USD\n"
                "      ≈ SGD +" + str(pnl_sgd) + "\n"
                "W/L: " + str(state["wins"]) + "/" + str(losses)
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
        next_open_sgt = "21:00 SGT"
        log.info("Outside NY session (13–16 UTC / 21–00 SGT) — next: " + next_open_sgt)
        return

    sess = ASSET["session"]
    log.info("Window: " + sess["label"] + " (13:00–16:00 UTC) | Max spread: " +
             str(sess["max_spread"]) + " pip")

    # ── Session open alert (once per day) ────────────────────────────
    alert_key = "ny_open_" + today
    if not state.get("session_alerted", {}).get(alert_key) and now_utc.hour == 13:
        state.setdefault("session_alerted", {})[alert_key] = True
        alert.send(
            "🔔 NY Session Open!\n"
            "⏰ 13:00 UTC (21:00 SGT)\n"
            "Pair: EUR/USD | TP=" + str(TP_PIPS) + "p SL=" + str(SL_PIPS) + "p\n"
            "Balance: $" + str(round(state.get("start_balance", 0), 2))
        )

    # ── Login ─────────────────────────────────────────────────────────
    trader = OandaTrader(demo=settings["demo_mode"])
    if not trader.login():
        log.warning("Login failed — skipping scan")
        return

    current_balance = trader.get_balance()
    if "start_balance" not in state or state["start_balance"] == 0.0:
        state["start_balance"] = current_balance

    detect_sl_tp_hits(state, trader, alert)

    name = ASSET["instrument"]

    # ── 45-min hard close ────────────────────────────────────────────
    pos = trader.get_position(name)
    if pos:
        try:
            trade_id, open_str = trader.get_open_trade_id(name)
            if trade_id and open_str:
                open_utc = datetime.fromisoformat(open_str.replace("Z", "+00:00"))
                mins     = (datetime.now(pytz.utc) - open_utc).total_seconds() / 60
                log.info(name + ": open " + str(round(mins, 1)) + " min")
                if mins >= MAX_DURATION:
                    pnl     = trader.check_pnl(pos)
                    pnl_sgd = round(pnl * USD_SGD, 2)
                    trader.close_position(name)
                    state.get("open_times", {}).pop(name, None)
                    if pnl < 0:
                        set_cooldown(state)
                    alert.send(
                        "⏰ 45-MIN TIMEOUT\n"
                        + ASSET["emoji"] + " EUR/USD\n"
                        "Closed at " + str(round(mins, 1)) + " min\n"
                        "PnL: $" + str(round(pnl, 2)) + " USD " +
                        ("✅" if pnl >= 0 else "🔴") + "\n"
                        "   ≈ SGD " + str(pnl_sgd)
                    )
        except Exception as e:
            log.warning("Duration check error: " + str(e))
        return  # don't scan while trade open

    # ── Daily trade limit ────────────────────────────────────────────
    today_trades = state.get("daily_trades", {}).get(today, 0)
    if today_trades >= MAX_PER_DAY:
        log.info("Daily limit reached (" + str(MAX_PER_DAY) + " trades) — done for today")
        return

    # ── Cooldown ─────────────────────────────────────────────────────
    if in_cooldown(state):
        log.info("In cooldown — " + str(cooldown_remaining(state)) + " min remaining")
        return

    # ── Price & spread check ─────────────────────────────────────────
    price, bid, ask = trader.get_price(name)
    if price is None:
        log.warning("Cannot get price — skipping")
        return

    spread_pip = (ask - bid) / ASSET["pip"]
    if spread_pip > sess["max_spread"] + 0.05:
        log.info("Spread " + str(round(spread_pip, 2)) + "p > max " +
                 str(sess["max_spread"]) + "p — skip")
        return

    # ── News filter ──────────────────────────────────────────────────
    news_active, news_reason = calendar.is_news_time(name)
    if news_active:
        news_key = name + "_news_" + now_sg.strftime("%Y%m%d%H")
        if not state.get("news_alerted", {}).get(news_key):
            state.setdefault("news_alerted", {})[news_key] = True
            alert.send("⚠️ NEWS BLOCK\n" + ASSET["emoji"] + " EUR/USD\n" +
                       news_reason + "\nSkipping trade")
        log.info("News block: " + news_reason)
        return

    # ── Signal scan ──────────────────────────────────────────────────
    threshold = settings.get("signal_threshold", 4)
    score, direction, details = signals.analyze(asset=ASSET["asset"], state=state)
    log.info(name + ": score=" + str(score) + "/" + str(threshold) +
             " dir=" + direction + " | " + details)

    if score < threshold or direction == "NONE":
        log.info(name + ": no setup — waiting for alignment")
        return

    # ── Place trade ──────────────────────────────────────────────────
    sl_sgd = round(TRADE_SIZE * SL_PIPS  * ASSET["pip"] * USD_SGD, 2)
    tp_sgd = round(TRADE_SIZE * TP_PIPS  * ASSET["pip"] * USD_SGD, 2)

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
        alert.send(
            "🔄 NEW TRADE!  [NY Session]\n"
            + ASSET["emoji"] + " EUR/USD\n"
            "Direction: " + direction + "\n"
            "Score:     " + str(score) + "/4 ✅\n"
            "Size:      74,000 units\n"
            "Entry:     " + str(round(price, ASSET["precision"])) + "\n"
            "SL:        " + str(SL_PIPS) + " pips ≈ SGD " + str(sl_sgd) + "\n"
            "TP:        " + str(TP_PIPS) + " pips ≈ SGD " + str(tp_sgd) + "\n"
            "Max Time:  45 min\n"
            "Spread:    " + str(round(spread_pip, 2)) + "p\n"
            "Day trade: " + str(today_trades + 1) + "/" + str(MAX_PER_DAY) + "\n"
            "Signals:   " + details
        )
        log.info(name + ": PLACED " + direction +
                 " SL=" + str(sl_sgd) + " TP=" + str(tp_sgd))
    else:
        set_cooldown(state)
        log.warning(name + ": order failed — " + str(result.get("error", "")))

    log.info("Scan complete.")
