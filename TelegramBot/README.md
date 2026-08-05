# TelegramBot/ — shared phone notifications for TradingBotApp

One Telegram bot, reusable from anywhere in this project, for anything
that should text Koko's phone: backtest done/failed, paper trading fills,
risk circuit-breaker trips, IBKR disconnects, launchd job failures, etc.

## Files

| File | Purpose |
|---|---|
| `notify.py` | The module. `send_telegram(text)` is the one function callers need. Also runnable directly: `python3 notify.py "message"` to send, or `python3 notify.py --get-chat-id <TOKEN>` during setup. |
Credentials live in **`secrets/telegram.env`** at the repo root, not in this
directory — see `secrets/README.md`. They hold `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` and are never committed. A legacy `TelegramBot/.env` still
works if you have one: `secrets_store.resolve()` falls back to it so an
unmigrated checkout keeps sending.

## One-time setup

1. In Telegram, message **@BotFather** → `/newbot` → follow the prompts. You get a token like `123456789:AAExampleTokenString`.
2. Send your new bot **any message** from your phone (bots can't message you first).
3. `cd TelegramBot && python3 notify.py --get-chat-id <TOKEN>` → prints your `chat_id`.
4. `cp secrets/telegram.env.example secrets/telegram.env` (from the repo root) and fill in both values.
5. Test: `python3 notify.py "hello from TradingBotApp"` — should land on your phone in a couple seconds.

## Using it from a new script anywhere in the project

```python
import sys
from pathlib import Path
# adjust the number of .parent calls to reach TradingBotApp/ from wherever
# your script lives, e.g. one level up from a script in the repo root,
# two levels up from a script in KronosAI/ or a similar subfolder
sys.path.insert(0, str(Path(__file__).parent / "TelegramBot"))
from notify import send_telegram

send_telegram("Paper trader: daily-loss circuit breaker tripped, halted.")
```

`send_telegram()` never raises by default (a dead notification shouldn't
crash the thing it's reporting on) — pass `raise_on_error=True` if a
caller specifically needs to know delivery failed. It retries twice on
network hiccups before giving up.

## Current callers

- `run_notify.sh <script> [args]` — generic wrapper for any one-shot script (backtests, a single research_agent.py ticker, etc.), from any session
- `KronosAI/run_kronos_backtest_notify.py` — Kronos backtest, with a richer hand-parsed summary (IC, hit rate, CAGR table)
- `run_research_agent_watchlist.py` — whole-watchlist research run, one consolidated digest
- `reflect_on_trades.py` — texts on each newly-closed paper position (win/loss)
- `ibkr_service.py` — texts when RiskGuard blocks a real order
- `paper_trader.py` — texts a summary after a real rebalance executes
- `daily_digest.py` — 07:30 local, morning plan from CLAUDE.md + freshness checks
