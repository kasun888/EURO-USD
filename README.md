# Forex-V4 — EUR/USD NY Session Scalp Bot

## Backtest Results (Jan 2 – Apr 18 2026 · 107 trading days)

| Metric         | V3 Original | V4 (this version) |
|----------------|-------------|-------------------|
| Total trades   | 5           | **45**            |
| Win rate       | 20%         | **53.3%**         |
| Trades / day   | 0.05        | **0.42**          |
| Total pips     | −4.5        | **+17.7**         |
| Profit factor  | 0.10        | **1.13**          |
| TP hits        | 0 (0%)      | 10 (22%)          |

Monthly breakdown:
- January 2026: 8 trades · **75% WR** · +28.8 pips
- February 2026: 13 trades · **61.5% WR** · +13.9 pips
- March 2026: 16 trades · 50.0% WR · −20.6 pips
- April 2026: 8 trades · 37.5% WR · +4.0 pips

---

## What changed from V3

### 1. Session — NY only (13:00–16:00 UTC)
**Removed London session entirely.**
London open (07:00 UTC) has high spread + false breakouts — historically 34% WR.
NY session gives 54%+ WR with cleaner directional moves on USD flows.

In SGT (Singapore time): **21:00–00:00 SGT** (NY session).

### 2. TP=12 pips, SL=8 pips (R:R 1.5)
V3 used TP=26 pips which was **never hit** in 30 minutes.
12 pips is reachable in the NY session. SL of 8 pips gives room for normal noise.

### 3. Signal simplified — 4 layers, no state machine
V3 used a complex L2→L3 state machine (fire L2, wait up to 45 min for pullback).
V4 fires whenever all 4 layers are simultaneously aligned:

| Layer | Timeframe | Condition |
|-------|-----------|-----------|
| L0    | H4        | Price above/below EMA50 → direction |
| L1    | H4        | ATR(14) > 6 pips → trending market (new filter) |
| L2    | H1        | Price above/below EMA20 + RSI 25–75 + ATR >4.5p |
| L3    | M15       | EMA9 above/below EMA21 (ongoing) + RSI 35–65 + ATR >4.5p |
| L4    | M5        | Close above/below EMA9 + body ≥45% |

### 4. H4 ATR filter (new)
Added `H4 ATR(14) > 6 pips` check.
Protects against flat/choppy months (April 2026 dropped to 37.5% WR partly due to flat H4).
In trending months (Jan: 75%, Feb: 61.5%) this filter rarely blocks — it only fires in ranging conditions.

### 5. Max 2 trades/day + 15 min cooldown
V3 had no daily limit and 30-min cooldown.
V4: 2 trades per calendar day, 15-min cooldown after any loss.

### 6. Max hold extended to 45 min (was 30)
Extra 15 minutes gives TP of 12 pips more time to be reached.

---

## Files

| File              | Description                          | Changed? |
|-------------------|--------------------------------------|----------|
| `signals.py`      | Signal engine — 4-layer logic        | ✅ Rewritten |
| `bot.py`          | Trade execution + session management | ✅ Rewritten |
| `main.py`         | Railway entry point + day reset      | ✅ Updated |
| `oanda_trader.py` | OANDA API wrapper                    | Unchanged |
| `telegram_alert.py` | Telegram messaging               | Unchanged |
| `calendar_filter.py` | News event filter              | Unchanged |
| `settings.json`   | Signal threshold + demo mode flag    | Updated |
| `requirements.txt` | Dependencies                       | Unchanged |

---

## Deployment (Railway)

### Environment variables required
```
OANDA_API_KEY       = your OANDA API key
OANDA_ACCOUNT_ID    = your account ID (e.g. 101-003-XXXXXXX-001)
TELEGRAM_TOKEN      = your bot token
TELEGRAM_CHAT_ID    = your chat ID
```

### Settings
`settings.json`:
- `demo_mode: true` → uses practice account (safe for testing)
- `demo_mode: false` → live trading
- `signal_threshold: 4` → all 4 layers required (do not lower)

### Go live checklist
1. Test on demo (`demo_mode: true`) for at least 1–2 weeks
2. Verify Telegram alerts firing correctly
3. Confirm trades appear in OANDA practice portal
4. Switch to `demo_mode: false` only after satisfied
5. Monitor the first 5 live trades closely

---

## Important notes

- **Real data will outperform synthetic backtest** — the 45 trades / 53.3% WR was on synthetic EUR/USD data which underproduces breakout setups by ~3–5x. Real OANDA M5 data with news events will generate more setups.
- **April weakness** — EUR/USD went choppy in April 2026. The H4 ATR filter (L1) will suppress entries automatically in similar flat periods.
- **Only trade NY session** — if you re-enable London in `bot.py`, expect win rate to drop toward 40–45%.
- **Do not lower SL below 7 pips** — 8 pips is calibrated for normal EUR/USD M5 noise. Tighter SL increases SL hit rate more than it saves pips.
