# TradingBotApp web UI

Local watch station for the FTMO trading venue. Next.js frontend in `web/`,
FastAPI backend in `../api/`.

```bash
./run_web.sh              # from the repo root — starts both
```

Then open http://localhost:3000. Watch is the landing screen.

## This is local-only, on purpose

**Never bind either process to `0.0.0.0` and never deploy this.** The backend
can arm the unattended runner; there is no auth layer because there is no
network exposure. Vercel is the framework here, not a deployment target.

## Architecture

```
web/  (Next.js 16, React 19, Tailwind v4, shadcn/Base UI, lightweight-charts)
  |  HTTP + one WebSocket (/ws/ftmo), localhost only
api/  (FastAPI)
  |  one lazily-opened cTrader session, on a Twisted thread
FTMO live endpoint  ->  Challenge account 48137229 (simulated capital)
```

**FTMO is the only venue.** IBKR was retired in place on 2026-08-02 and removed
entirely on 2026-08-09 — its modules, its routes, its three screens, its
WebSocket hub and its two launchd jobs are all gone. Nothing here dials IB
Gateway, which is why the backend no longer has a connection hub, a write
worker, a paper-account gate or a symbol resolver. Its 46 journal rows survive
in the Ledger, labelled `ibkr`: an audit trail you prune when a venue is
retired is not an audit trail.

The backend is a thin wrapper by design. Risk decisions, sizing, journalling
and indicator math all stay in the Python modules at the repo root —
`ftmo_rules.py`, `ftmo_sizing.py`, `trade_journal.py`, `indicators.py` — so the
browser path and the terminal path cannot diverge about whether the account is
safe. **This UI places no orders.** It reads, previews what the runner would
do, and arms or disarms it; `ftmo_runner.py` is the only thing that trades.

## Screens

Four routes since 2026-08-09, down from eight. Three of the old ones were a
dimmed IBKR section for a venue that placed no orders, and went with it. Every
old URL still works — `next.config.ts` redirects them, and the ones that became
tabs carry `?tab=` so a bookmark lands where it used to.

| Route | What it does |
|---|---|
| `/watch` | The station, and the landing screen. Equity / balance / floating, the **night band**, the three limit meters, and open positions. |
| `/signal` | `?tab=forecast` Kronos ranking across N independent draws, spread per ticker, top-N boundary warning, forecast chart, Monte Carlo fan · `?tab=plan` the FTMO plan it would produce right now, computed by the same pipeline the runner uses and stopping before it places anything. |
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

## This UI places no orders

There is no order path in the browser any more. The preview → execute(token)
write flow, the dedicated order worker thread and the bracket dialog all
belonged to IBKR and were removed with it on 2026-08-09.

What is left is one write: **arming or disarming `ftmo_runner.py`**, from the
header switch. It posts to `/api/ftmo/autotrade`, which edits
`trader_settings.json`; the runner re-reads that on every wakeup, so the change
takes effect from the next firing without touching launchd.

Two properties of that switch worth keeping:

- **It is not gated on the venue being reachable.** A kill switch that needs a
  healthy connection is not a kill switch.
- **Disarming needs no confirmation; arming does.** The worst case of turning
  it off is that the experiment pauses. Turning it on gets a dialog that states
  what the evidence actually says — all four asset classes failed their IC
  screen twice — because this runs against the project's own findings by
  deliberate exception, and the UI should say so at the moment it matters.

## Design rules this UI follows

These are not stylistic preferences — each one exists because the naive
version already failed in production on this account.

**1. Unknown is a third state, never folded into "no".**
`armed: null` means the arm toggle could not be read; it renders as its own
state, never as "disarmed". The rule was learned on stop protection: on
2026-07-29 a wedged broker answered position requests normally while the
open-orders request timed out, and conflating "did not answer" with "no stop"
would have shown four naked positions that were fully protected the whole time.

**2. A pending request never renders as a fact.**
Tiles show `—` until the venue has actually answered. An in-flight positions
fetch once painted a green "No exposure" tile over three live positions. A
position with no quote is reported as unpriced and excluded from floating P&L,
and the screen says the equity is *incomplete*, not flat.

**3. A firing that did not happen is drawn, not omitted.**
The night band's `missed` cells are hours the trading window was open and the
runner left no audit record. 22 consecutive firings were lost to the Mac
sleeping on battery and went unnoticed for 19 hours; a band that quietly
skipped them would draw a tidy line through a night when nothing was watching.

**4. Every limit is measured on equity, and the UI shows equity.**
FTMO limits count balance *plus floating P&L*, so the account can breach with
no order placed. The header and the Watch readout lead with equity for that
reason — balance is the number that cannot breach anything.

**5. Indicator math never happens in JavaScript.**
Every overlay is computed server-side by `indicators.py`, so the chart, the
research agent's notes and the terminal app can never disagree about what
RSI(14) was.

**6. The journal shows its own corrections.**
Superseded and disputed rows stay visible, struck through and labelled.
Deleting them would destroy the audit trail; the contradiction is the
information. The same applies to the retired venue: `venue=ibkr` rows are still
served and still labelled.

**7. Symbol suggestions come from the venue's own capture.**
The chart's box filters `ftmo_symbol_specs.json` — all 202 instruments the
broker actually carries. It previously fell back to a broker contract search
that happily suggested `SPY` and `NVDA`, which resolved fine and then failed at
FTMO with `not in the symbol capture`, reading as a broken chart rather than as
a symbol that was never available.

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

Backend self-tests, all offline — no venue connection, no credentials:

```bash
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
