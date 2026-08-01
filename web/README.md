# TradingBotApp web UI

Local control panel for the IBKR paper trading system. Next.js frontend in
`web/`, FastAPI backend in `../api/`.

```bash
./run_web.sh              # from the repo root — starts both
```

Then open http://localhost:3000. Charts is the landing screen.

## This is local-only, on purpose

**Never bind either process to `0.0.0.0` and never deploy this.** The backend
holds a live IB Gateway connection and can place orders; there is no auth
layer because there is no network exposure. Vercel is the framework here, not
a deployment target.

## Architecture

```
web/  (Next.js 16, React 19, Tailwind v4, shadcn/Base UI, lightweight-charts)
  |  HTTP + WebSocket, localhost only
api/  (FastAPI)
  |  one persistent ib_async connection, clientId 15
IB Gateway :4002  ->  paper account DUQ903866
```

`clientId 15` is distinct from trader_app (7), paper_trader (9),
reflect_on_trades (11) and autotrade_runner (13), so the UI can run at the
same time as any of them without fighting for a session.

The backend is a thin wrapper by design. Order placement, risk checks,
sizing, journalling and indicator math all stay in the existing Python
modules — `ibkr_service.py`, `paper_trader.py`, `indicators.py` — so the
browser path and the terminal path cannot diverge in risk handling. That is
the same reasoning that made `execute_rebalance` shared between the
human-approved and autotrade paths.

## Screens

| Route | What it does |
|---|---|
| `/charts` | Candles for stocks, ETFs, forex, crypto and futures. 1m–1d, indicators.py overlays and sub-panes, journal trade markers, live GTC stop lines. |
| `/dashboard` | Net liquidation (USD-converted), unrealised P&L, stop-health verdict, RiskGuard limits, signal policy, journal counts. |
| `/positions` | Open positions with per-position stop verdict, plus live open orders. |
| `/journal` | trade_journal.csv with corrections and phantom rows marked. |
| `/kronos`, `/backtests` | Not built yet. |

## Design rules this UI follows

These are not stylistic preferences — each one exists because the naive
version already failed in production on this account.

**1. Unknown is a third state, never folded into "no".**
`protected: null` means IBKR did not answer the open-orders request. It
renders amber as `UNKNOWN`, never red as `UNPROTECTED`. On 2026-07-29 a
wedged Gateway answered position requests normally while `reqAllOpenOrders`
timed out — conflating the two would have shown four naked positions that
were fully protected the whole time.

**2. A pending request never renders as a fact.**
Summary tiles show `—` until IBKR has actually answered. An in-flight
positions fetch once painted a green "No exposure" tile over three live
positions, which is the same class of error as the phantom liquidation in
`reflect_on_trades.py`.

**3. "Protected" means GTC, not "a stop exists".**
A DAY stop expires at the session close and stops protecting anything. It is
drawn dashed and amber on the chart and counts as unprotected in the verdict,
which comes from `ibkr_service.stop_protection_status` — the same function
the terminal paths use.

**4. Market data is labelled with its source and age.**
This account has no live market-data subscription, so everything is delayed
(`reqMarketDataType(3)`). Every chart says `DELAYED`, names the source, and
shows how old the pull is.

**5. Indicator math never happens in JavaScript.**
Every overlay is computed server-side by `indicators.py`, so the chart, the
research agent's notes and the terminal app can never disagree about what
RSI(14) was.

**6. The journal shows its own corrections.**
Superseded and disputed rows stay visible, struck through and labelled.
Deleting them would destroy the audit trail; the contradiction is the
information.

## Development

```bash
cd web
npm run dev          # frontend only (expects the API on :8000)
npx tsc --noEmit     # typecheck
```

Backend self-tests, all offline — no IBKR connection needed:

```bash
.venv/bin/python api/contracts.py        # symbol parsing across asset classes
.venv/bin/python api/indicators_api.py   # parity with indicators.py
.venv/bin/python api/journal_api.py      # correction/phantom detection
```

Next.js 16 ships its own docs at `node_modules/next/dist/docs/` and warns
they differ from what a model may have memorised — read those before
changing framework-level code. Note shadcn now generates **Base UI**
components, which compose with a `render` prop, not Radix's `asChild`.
