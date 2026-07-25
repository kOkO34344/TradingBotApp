---
tags: [watchlist, infrastructure, ibkr]
source: watchlist.py
status: "Live — named groups, validated symbols"
last_updated: 2026-07-25
---

# Watchlist Context

## What it is now

Built 2026-07-25. The watchlist used to be a flat list, edited in
`trader_app.py`'s Settings menu as a raw comma-separated string. It's now
stored as **named groups** (`trader_settings.json`'s `watchlist_groups`),
edited from a dedicated menu (`trader_app.py` main menu 9), with
`settings["tickers"]` derived as the deduped union of every group and
regenerated on every save.

Groups are the source of truth; `tickers` stays the contract every consumer
already reads (`paper_trader.py`, `autotrade_runner.py`,
`run_research_agent_watchlist.py`, `kronos_agent.py`, `trader_app.py` itself)
— nothing downstream had to change.

**Current groups:** one, `Core`, holding all 14 tickers: AAPL, MSFT, GOOGL,
AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR, AVGO, ASML. (AVGO/ASML were
added outside a session, before this feature existed — the migration path
picked them up automatically with no manual re-entry needed.)

## Why this exists — the request that triggered it

The owner asked for the app's watchlist to always match "every watchlist I
have or will have in IBKR." That turned out to be unbuildable as literally
stated:

**The TWS API has no watchlist endpoint.** Verified directly against
`ib_async` 2.1.0's `IB` class surface: there's `reqScannerData`,
`reqPositions`, `reqContractDetails` — nothing that reads a TWS watchlist.
They're a client-side TWS UI feature, full stop. True auto-sync would need
IBKR's separate **Client Portal Web API** — a second Java gateway process,
browser-based login, and a session-keepalive daemon. That's real ongoing
infrastructure, not a small addition, and was deliberately not built.

Given that constraint, the owner chose (via a clarifying-question pass):
- **Mechanism:** manual editing in the app, made smarter (validation,
  dedup, position-safety checks) rather than a sync layer.
- **Trade scope:** one universe — everything in the watchlist is tradeable,
  not split into "watch" vs "trade" tiers.
- **Structure:** named groups, mirroring how IBKR watchlists are organized,
  even without a live sync to them.
- **Non-equities:** dropped and reported, never silently discarded.

## Two guards this adds, both verified against the live paper account

**1. Symbol validation on entry.** Every added symbol is checked against
yfinance AND against what the order path can actually route (US `STK`
contracts only). Tested live with a deliberately mixed set:

| Input | Result |
|---|---|
| `BRK B` | normalized and kept as `BRK-B` |
| `EUR.USD` | dropped — FX pair |
| `9988.HK` | dropped — foreign listing |
| `BTC-USD` | dropped — crypto pair |
| `ES=F` | dropped — futures contract |
| `FAKETICKER99` | dropped — no yfinance data |

Everything dropped is reported with a reason, never silently discarded —
same precedent `broad_universe_momentum.py` already set for unfetchable
tickers.

**2. Removal is guarded against a live position.** `paper_trader.py` filters
current holdings with `if sym in tickers` — so removing a symbol you still
hold makes that position **invisible** to it: the GTC stop survives, but
nothing ever manages or exits the position again. The watchlist menu checks
`ib.positions()` (read-only, `readonly=True`) before letting a removal
through, and warns loudly if the symbol is held. Verified live: attempting
to remove AAPL while it was an open position correctly surfaced the warning
and required explicit confirmation.

## A design trap avoided

`DEFAULT_SETTINGS` deliberately carries no `watchlist_groups` key. This
project's `load_settings()` shallow-merges saved settings over defaults — a
default groups dict would have overridden an *existing* install's real
watchlist while its longer, correct `tickers` list survived untouched,
silently shrinking the traded universe on the very next save. Leaving the
key absent means `watchlist.get_groups()` migrates from the existing
`tickers` list instead, which is correct whether the install is brand new or
years old. Caught by testing the migration path before shipping, not by
code review alone.

## Files

- `watchlist.py` — groups/flatten/validate/held-position logic; has an
  offline `python3 watchlist.py` selftest (no network, no IBKR)
- `trader_app.py` — menu 9 (`watchlist_menu`), replacing the old raw-string
  ticker edit in Settings (menu 10 now), which was removed rather than kept
  alongside — writing `tickers` directly there would desync it from the
  groups and be silently reverted on the next group save
- `run_research_agent_watchlist.py` — `--group <name>` / `--list-groups`
  flags, for refreshing research on one group instead of the whole
  watchlist (this file is currently untracked, see below)

## Provenance note

`--group` / `--list-groups` are written and working, but live in
`run_research_agent_watchlist.py`, which — along with `TelegramBot/` it
depends on — originated in another session and was only committed
2026-07-25 alongside this feature. `daily_digest.py`/`.sh` and
`run_notify.py`/`.sh` remain untracked as of this note.

## Related Notes

- [[00 MOC - Trading Bot Vault]]
- [[IBKR Integration]] — why the TWS API can't serve watchlists, connection
  basics
- [[Risk Management System]] — why an invisible-position removal matters
- [[Autotrade (Experimental)]] — the traded universe this watchlist now
  feeds directly, with no "watch-only" tier
