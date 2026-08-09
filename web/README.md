# TradingBotApp web UI

Local control panel for the IBKR paper trading system. Next.js frontend in
`web/`, FastAPI backend in `../api/`.

```bash
./run_web.sh              # from the repo root — starts both
```

Then open http://localhost:3000. Watch is the landing screen.

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

Four routes since 2026-08-09, down from eight. The old nav carried three
dimmed IBKR entries for a venue rule 9 retired, so a third of the app
advertised a broker that places no orders. Every old URL still works —
`next.config.ts` redirects them, and the six that became tabs carry `?tab=` so
a bookmark lands where it used to.

| Route | What it does |
|---|---|
| `/watch` | The station, and the landing screen. Equity / balance / floating, the **night band**, the three limit meters, open positions, and IBKR as a collapsed "retired" drawer holding its positions and account screens. |
| `/signal` | `?tab=forecast` Kronos ranking across N independent draws, spread per ticker, top-N boundary warning, forecast chart, Monte Carlo fan · `?tab=plan` the FTMO plan it produces · `?tab=rotation` the IBKR proposal and its approve/decline. |
| `/market` | Candles for stocks, ETFs, forex, crypto and futures. 1m–1d, indicators.py overlays and sub-panes, journal trade markers, live stop lines. Symbol box has typeahead, searchable by company name. |
| `/ledger` | `?tab=journal` trade_journal.csv with corrections and phantom rows marked · `?tab=backtests` recorded results, in-sample and out-of-sample kept apart, quoted findings marked as quoted. |

### The night band

The signature instrument on `/watch`. One session — 16:30 Sofia through 11:30
the next morning — reconstructed from `ftmo_audit/*.jsonl` by
`/api/ftmo/timeline`, in three lanes over one time axis: equity auto-scaled to
its own range, the daily-loss reservoir at true scale with its thresholds
marked, and one cell per hourly wakeup.

**The wakeup lane is the reason it exists.** A slot the window was open for
with no audit record is a firing that never ran, and it is drawn struck and
amber rather than left blank. This project lost 22 consecutive firings to the
Mac sleeping on battery and did not notice for 19 hours; the band is where that
becomes visible in one glance. A record inside a *closed* slot is `forced` — a
`--force` run, a `--reconcile`, or a plan previewed from this dashboard — and
is never drawn as a scheduled firing.

The endpoint reads the audit trail off disk with no venue session, so it still
answers when the broker is unreachable. That is deliberate: "what did the
runner do overnight" is exactly the question you ask when the venue is down.

## Write actions

Every write is **preview → execute(token)**. The preview returns the exact
order plus RiskGuard's verdict; execute carries only the token and the
backend reads the parameters back from the stored preview. The browser
therefore cannot display one order and submit another. Previews expire after
120 seconds rather than silently repricing.

Order placement runs on a dedicated worker thread with its own IB connection
(clientId 16), because `ibkr_service`'s order functions are synchronous and
`ib.sleep()` → `IB.run()` → `run_until_complete()` cannot execute inside the
server's event loop. Rewriting them async would fork the risk-handling code;
running them unmodified on their own thread does not.

Entries are bracket-only. There is no un-stopped entry path in this UI and
there will not be one.

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

**7. Symbol suggestions come from IBKR, not a local list.**
`/api/symbols/search` merges watchlist matches with `reqMatchingSymbols`, so
it only ever offers instruments this account can actually trade. Each row
carries the exact query string the chart will receive and shows it when it
differs from the plain symbol — `NVDA` and `STK:NVDA:MXN` are different
instruments that look identical in a bare list. Futures and index rows are
dropped rather than guessed at, because the box cannot express an expiry.

## Development

```bash
cd web
npm run dev                        # frontend only (expects the API on :8000)
./node_modules/.bin/tsc --noEmit   # typecheck
./node_modules/.bin/eslint .       # lint
```

`npx tsc` does NOT work here — TypeScript is a transitive dependency with no
`npx` shim, and npx answers with "This is not the tsc command you are looking
for", which reads like a broken install. Call the local binary directly.

After moving or adding a route, run `./node_modules/.bin/next typegen` (or
delete `.next/`) before typechecking. The generated route types are stale
otherwise and `tsc` reports missing modules for pages you deliberately removed.

Backend self-tests, all offline — no IBKR connection needed:

```bash
.venv/bin/python api/contracts.py        # symbol parsing across asset classes
.venv/bin/python api/indicators_api.py   # parity with indicators.py
.venv/bin/python api/journal_api.py      # correction/phantom detection
.venv/bin/python api/backtests_api.py    # in/out-of-sample parsing
.venv/bin/python api/ftmo_api.py --selftest   # night-band session reconstruction
```

Next.js 16 ships its own docs at `node_modules/next/dist/docs/` and warns
they differ from what a model may have memorised — read those before
changing framework-level code.

shadcn now generates **Base UI** components. Three differences bite, and only
the first fails typechecking: compose with `render` not `asChild`; menu items
fire `onClick` not `onSelect` (an `onSelect` type-checks and silently never
runs); and `DropdownMenuLabel` throws unless wrapped in `DropdownMenuGroup`.
Click any shadcn component you touch — `tsc` clean proves little here.
