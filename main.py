"""
Railway Entry Point — EUR/USD NY Scalp Bot V7-PLUS
====================================================
Session:  NY only — 13:00–16:00 UTC (21:00–00:00 SGT)
Strategy: V7-PLUS — 4-layer trend confirm + circuit breaker
  SL=7 pips (≈SGD 70) | TP=10 pips (≈SGD 100) | R:R 1.43
  Max 1 trade/day | 45 min hold | Circuit breaker after 2 SL

All Telegram alerts show SGD amounts and live balance.
"""

import os
import time
import logging
import traceback
from datetime import datetime

import pytz

from bot            import run_bot, ASSET, is_in_session
from oanda_trader   import OandaTrader
from telegram_alert import TelegramAlert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger(__name__)

INTERVAL_MINUTES = 5
sg_tz            = pytz.timezone("Asia/Singapore")
STATE            = {}


def get_today():
    return datetime.now(sg_tz).strftime("%Y%m%d")


def fresh_day_state(today_str, balance):
    return {
        "date":            today_str,
        "trades":          0,
        "start_balance":   balance,
        "wins":            0,
        "losses":          0,
        "consec_losses":   0,
        "consec_sl":       0,
        "pause_until":     None,
        "cooldown_until":  None,
        "daily_trades":    {},
        "open_times":      {},
        "news_alerted":    {},
        "session_alerted": {},
    }


def check_env():
    api_key    = os.environ.get("OANDA_API_KEY", "")
    account_id = os.environ.get("OANDA_ACCOUNT_ID", "")
    tg_token   = os.environ.get("TELEGRAM_TOKEN", "")
    tg_chat    = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not api_key or not account_id:
        log.error("=" * 50)
        log.error("❌ MISSING OANDA ENV VARS!")
        log.error("   OANDA_API_KEY    : " + ("SET ✅" if api_key    else "MISSING ❌"))
        log.error("   OANDA_ACCOUNT_ID : " + ("SET ✅" if account_id else "MISSING ❌"))
        log.error("=" * 50)
        return False

    log.info("Env vars OK | Key: " + api_key[:8] + "**** | Account: " + account_id)
    if not tg_token or not tg_chat:
        log.warning("Telegram not configured — no alerts will be sent")
    return True


def main():
    global STATE

    log.info("=" * 55)
    log.info("🚀 EUR/USD Bot V7-PLUS Started")
    log.info("Session:  NY only — 13:00–16:00 UTC (21:00–00:00 SGT)")
    log.info("SL=7p (≈SGD 70) | TP=10p (≈SGD 100) | R:R=1.43")
    log.info("Max 1 trade/day | Circuit breaker after 2 SL hits")
    log.info("=" * 55)

    if not check_env():
        log.error("Missing env vars — sleeping 60s then exiting")
        time.sleep(60)
        return

    alert = TelegramAlert()
    alert.send(
        "🚀 EUR/USD Bot V7-PLUS Started!\n"
        "Strategy: 4-Layer NY Trend Scalp\n"
        "Pair:     EUR/USD\n"
        "SL: 7 pip ≈ SGD 70\n"
        "TP: 10 pip ≈ SGD 100\n"
        "R:R: 1.43\n"
        "Session: NY only — 21:00–00:00 SGT\n"
        "Max: 1 trade/day | 45 min hold\n"
        "Circuit breaker: pause 2 days after 2 SL in a row"
    )

    while True:
        try:
            now   = datetime.now(sg_tz)
            today = now.strftime("%Y%m%d")

            log.info("⏰ " + now.strftime("%Y-%m-%d %H:%M SGT"))

            # Day reset — preserve circuit breaker + consec_sl across days
            if STATE.get("date") != today:
                log.info("📅 New day — resetting state")
                try:
                    trader  = OandaTrader(demo=True)
                    balance = trader.get_balance() if trader.login() else 0.0
                except Exception as e:
                    log.warning("Balance fetch error: " + str(e))
                    balance = 0.0
                log.info("Balance: SGD " + str(round(balance * 1.35, 2)))
                # Preserve circuit breaker across day reset
                prev_pause = STATE.get("pause_until")
                prev_consec_sl = STATE.get("consec_sl", 0)
                STATE = fresh_day_state(today, balance)
                if prev_pause:
                    STATE["pause_until"] = prev_pause
                STATE["consec_sl"] = prev_consec_sl

            run_bot(state=STATE)

        except Exception as e:
            log.error("❌ Bot error: " + str(e))
            log.error(traceback.format_exc())
            time.sleep(30)

        log.info("💤 Sleeping " + str(INTERVAL_MINUTES) + " min...")
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
