---
name: research-cycle
description: Run the weekly research cycle — refresh the watchlist notes, re-strike the grades, and read the calibration report honestly against its null. Use when the research agent is overdue (cadence is 7 days), when asked to run research or grade calls, or when someone wants to know whether the project has any evidence yet.
disable-model-invocation: true
---

# Research cycle

**This is the bottleneck.** Not the dashboard, not the venue plumbing. Every
phase gate in this project is gated on graded evidence, and the thing that
produces evidence is this cycle. It slips constantly — check
`research_log/` before assuming otherwise.

## The cycle

### 1. Refresh the notes

```bash
./run_research_agent_watchlist.sh
```

**Do NOT wrap this in `run_notify.sh`** — it notifies from inside itself, and
wrapping it double-texts. Same for `ftmo_runner.py`, `ftmo_closes.py` and
`daily_digest.py`. (Anything that does *not* self-notify should be wrapped:
`./run_notify.sh <script> [args]`.)

Only part of the universe needs refreshing? `--group <name>` and
`--list-groups` work here. Groups are the source of truth in
`trader_settings.json` → `watchlist_groups`; `tickers` is derived and
regenerated on save. Edit via `trader_app.py` menu 9 only.

Remember the watchlist is the **research** universe, not the traded one. What
FTMO can trade is a different set entirely (`EURUSD`, `US30.cash`,
`NATGAS.cash`), derived by `ftmo_signal.build_universe`.

### 2. Re-strike the grades

```bash
.venv/bin/python3 grade_calls.py --csv
```

Grades are struck once and cached in `grading_cache.json`, deliberately:
`forward_return()` re-downloaded from yfinance every run, and yfinance returns
slightly different bars run to run — three consecutive runs scored the same
book 37% / 34% / 37%. **A file the autonomy bar is read from cannot change
when you re-read it.** Use `--refresh` only when you mean to re-strike.

This buys reproducibility, not accuracy.

### 3. Read the report — this is the part that gets skipped

Do not just run it. Read it, and report it honestly.

- **A win rate without its null is not evidence.** `grade_calls.py` prints the
  chance base rate, the edge over it, and a binomial p-value on every line.
  Never quote a win rate without the number it is being compared against.
- The standing result, as of 2026-08-03: **38 graded at 5d, 26% correct against
  a 39% chance base rate, p=0.13** — indistinguishable from guessing in either
  direction. Not evidence the agent is bad; evidence that there is still almost
  no evidence.
- **All 38 calls share one market week.** They are heavily correlated — closer
  to one observation than to 38. Say this whenever the number is quoted.
- Treat any report claiming grades from notes that are **not in
  `research_log/`** as corrupt. That is not hypothetical: four grades survived
  from two deleted synthetic test notes and `daily_digest.py` reported them as
  a track record for days, in the one file that exists to gate autonomy.

### 4. Record it

Append the result to `research_log/` and update CLAUDE.md's Work Queue item 3
if the numbers moved. **Negative results get reported, not massaged** — rule 4.
Both digests quote the Work Queue and Empirical Findings close to verbatim, so
stale text there propagates to the phone.

## Traps in this cycle

- **`forward_return()` needs `days + 1` bars**, so a 5d horizon needs **6**
  sessions from the note date, not 5. Calls that look stuck as "pending" are
  usually genuinely pending — verify against the underlying yfinance data
  before calling it a fetch bug.
- **2026-07-24 (a Friday) has no bar in yfinance for any ticker.** Trading-day
  arithmetic across that window is off by one if you assume a normal week.
- **Never write synthetic notes into `research_log/`.** If you need them for
  testing, write them to a temp dir.
- **The grading band is 0.5x that ticker's realized sigma at that horizon**,
  measured strictly before the note date — not a fixed ±2%. One fixed band
  across horizons differing 4x in length measures nothing: a no-edge call lands
  inside ±2% about 42% of the time by chance at 5d and 21% at 21d, and 5d sigma
  across the watchlist runs 2.4% (KO) to 9.2% (PLTR). The legacy fixed-band
  grade is printed alongside so the change stays auditable.
- Changing a metric after seeing a bad result is the shape rule 4 forbids. The
  band change was allowed only because the flaw was provable from price history
  *without reference to any grade*. Hold any future metric change to that test.

## Long runs

Anything that will take a while goes through the notifier so you are not
watching a terminal:

```bash
./run_notify.sh KronosAI/kronos_ic_assetclass.py
nohup ./run_notify.sh <script> [args] > /dev/null 2>&1 & disown
```

See the `notify-on-long-runs` skill for which scripts self-notify and must not
be wrapped.
