---
tags: [research, grading, tracker, weekly]
status: "38 real grades at 5d as of 2026-08-03 — no detectable skill (26% vs 39% chance, p=0.13); 38 pending at 21d"
last_updated: 2026-08-05
---

# Graded Calls Tracker

Weekly calibration results from `grade_calls.py --csv`. This is the live evidence of whether the research agent is any good.

**Next action:** the 38 calls graded at 5d on 2026-08-03 are pending at 21d.
Re-run `.venv/bin/python grade_calls.py --csv` once those mature and add a row.
Also re-run `run_research_agent_watchlist.py` — every note in `research_log/`
still dates from 2026-07-25, so the book has not grown.

> [!danger] Any pre-2026-07-28 numbers in this file are void
> The 4 grades that existed until then came from two **synthetic test
> notes**, not real calls — see [[Call Grading System]]. The project went from
> **0 real graded calls** to its first 38 on **2026-08-03**.

> [!warning] Correction — a no-edge book is NOT "cheap to be right about"
> An earlier version of this note said a mostly-no-edge, low-confidence book
> would be easy to score well under the ±2% flat band, so the headline win rate
> would flatter the agent. **That was exactly backwards, and it mattered.**
> Measured from price history alone, a no-edge call landed inside ±2% only
> ~42% of the time by chance at 5d and ~21% at 21d — the flat band made a
> no-edge book *hard* to be right about, increasingly so at longer horizons.
> The band is now 0.5× the ticker's realized sigma at that horizon. Details in
> the methodology section below.

---

## Grading Schedule

Every **Sunday evening** or **Monday morning**, run:

```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 grade_calls.py --csv
```

Then copy the output below.

---

## Weekly Results

| Date | Notes Graded | 5d Correct | Chance base rate | Edge | Binomial p | 21d | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| 2026-07-25 | 0 | — | — | — | — | — | pending — notes too fresh |
| **2026-08-03** | **38** | **26%** | **39%** | **-13pt** | **0.13** | 38 pending | **no detectable skill, in either direction** |

## The first real grades landed 2026-08-03 — and the answer is "we still know almost nothing"

38 calls graded at the 5-day horizon. **26% correct against a chance base rate
of 39%** — an edge of **-13 points** at exact binomial **p = 0.13**, which is
indistinguishable from guessing in *either* direction. By direction:

| direction | n | correct | chance |
|---|---:|---:|---:|
| long | 6 | 33% | 35% |
| no-edge | 28 | 29% | 42% |
| short | 4 | 0% | 25% |

**Read the sample honestly before concluding anything: all 38 calls share one
market week, so they are heavily correlated. This is much closer to ONE
observation than to 38.** It is not evidence the agent is bad. It is evidence
that we still have almost no evidence. 38 calls remain pending at 21d.

Calibration points the healthy way — low confidence (1-4) 22%, mid (5-6) 40%,
nothing ever rated above 6/10 — on n=27 and n=10 respectively. Far too small to
bank.

Book shape: **74% no-edge** (28/38), 16% long, 11% short, with confidence
clustered low (18 calls at 3/10).

> [!warning] A win rate without its null is not evidence
> `grade_calls.py` now prints the chance base rate, the edge over it, and a
> binomial p-value on every line. **Never quote a win rate from this project
> without the number it is being compared against.** A bare "26% correct" reads
> as catastrophic; against a 39% null on n=38 correlated calls it reads as
> noise, which is the truth.

> [!danger] The 4 grades this file used to show were fabricated
> Until 2026-07-28 `graded_calls.csv` carried 4 grades that came from two
> **synthetic test notes** (each literally headed "SYNTHETIC TEST NOTE — not a
> real call") deleted when real runs started. The CSV kept their grades and
> `daily_digest.py` reported "4 graded, 0 pending" every morning for days —
> fabricated calibration evidence presented as a track record, in the one file
> that exists to gate autonomy. Never put synthetic notes in `research_log/`;
> write them to a temp dir if you need them.

### Two methodology fixes that came with these grades

**Grades are struck once and cached** in `grading_cache.json` (tracked).
`forward_return()` re-downloaded from yfinance every run and yfinance returns
slightly different bars run to run — three consecutive runs on identical notes
scored the same book 37% / 34% / 37%. A file the autonomy bar is read from
cannot change when you re-read it. `--refresh` re-strikes deliberately. This
buys **reproducibility, not accuracy**.

**The flat ±2% band was replaced by 0.5× that ticker's realized sigma** at that
horizon, measured strictly before the note date. The old note here claimed a
mostly-no-edge book was "cheap to be right about" under a flat band. That was
exactly backwards: measured from 2y of price history alone, a no-edge call
landed inside ±2% only ~42% of the time by chance at 5d and ~21% at 21d, so one
fixed band across horizons differing 4× in length would have printed ~21% on
the pending 21d book and read as catastrophic failure while measuring nothing.
And 5d sigma across the watchlist runs 2.4% (KO) to 9.2% (PLTR), so the same
call at the same confidence was being graded on *which ticker it was handed*.

Changing a metric after seeing a bad result is the shape of what the
honest-backtesting rule forbids. It was allowed here only because the flaw is
provable from price history **without reference to any grade**, and the legacy
fixed-band grade is printed alongside so the change stays auditable. Hold any
future metric change to that same test.

---

## How to Read This Table

- **Notes Graded:** How many of the 12 have enough future price data to grade (≥5 or ≥21 days)
- **5d Correct / 21d Correct:** Percentage of graded calls that matched their direction within the threshold
- **5d Sharpe / 21d Sharpe:** Not a true Sharpe ratio, but a quick hit-rate view per horizon
- **High/Mid/Low Conf:** Accuracy by confidence bucket (1–4=low, 5–6=mid, 7–10=high)
- **Verdict:** ✅ Good, 🟡 Watch, ❌ Problem

### Interpretation Thresholds

✅ **Green light** (agent is useful):
- Long > 65%, short > 50%, no-edge > 50%
- High-conf > mid-conf > low-conf (proper calibration)
- Consistent over 2+ weeks

🟡 **Yellow flag** (keep watching):
- Long 55–65%, short < 50%
- Confidence buckets are close (e.g., high 58%, mid 62%)
- Small sample size (< 20 data points per bucket)

❌ **Red flag** (needs fixing):
- Overall < 50% accuracy
- High-conf ≈ low-conf (confidence is noise)
- Multiple weeks of degradation

---

## Notes On Calibration

### Why confidence matters

A model that's 60% correct at high confidence is **better calibrated** than one that's 60% correct at low confidence, because it's learned to be more confident when it's actually right.

If you see this:
```
High confidence: 52% correct
Low confidence: 60% correct
```

The model is **inverted** — it's confident about the wrong things. This is worse than random, because you'll follow it when it's most likely wrong.

### Survivor bias

These 12 tickers are mega-caps that survived to 2026. A fair validation would use a dynamic S&P 500 constituent list. Early grading will likely show optimistic accuracy (survivorship bias). When Phase 4 (live trading) starts, revisit calibration on a broader list.

### Market regime

A model trained in a bull market (which this backtest period is) will have a long bias. In 2026-Q3, check if the agent is actually getting short calls right (test data is weak here). By end of 2026, if we hit a bear market, re-grade with that in mind.

---

## Action Triggers

| Observation | Action |
|---|---|
| < 5 data points for a week | Keep collecting; no action needed yet |
| High conf > 70%, mid conf > 60% for 3+ weeks | ✅ Ready for paper trading (Phase 3) |
| High conf < 55% for 2+ weeks | ⚠️ Pause Phase 3 plans; revisit agent prompt or data quality |
| Confidence buckets inverted for 2+ weeks | ❌ Rebuild agent; don't trust for live trading |
| Long >> short (e.g., long 75%, short 10%) for 3+ weeks | ⚠️ Check for market bias; might miss opportunities in a downturn |

---

## Grading Output Template

After running `grade_calls.py --csv`, copy the report here:

```
=== CALL GRADING REPORT — [DATE] ===
Notes: [N]   graded: [N]   pending (too recent): [N]

-- 5d horizon: [%] correct ([N]/[N])
   long       [%] correct  (n=[N])
   short      [%] correct  (n=[N])
   no-edge    [%] correct  (n=[N])

-- 21d horizon: [%] correct ([N]/[N])
   long       [%] correct  (n=[N])
   short      [%] correct  (n=[N])
   no-edge    [%] correct  (n=[N])

-- Calibration by confidence (all horizons):
   low 1-4    [%] correct  (n=[N])
   mid 5-6    [%] correct  (n=[N])
   high 7-10  [%] correct  (n=[N])
```

---

## Related Notes

- [[Call Grading System]] — how grading works, thresholds, interpretation
- [[Research Agent Workflow]] — how the agent generates notes
- [[Phase Milestones Dashboard]] — Phase 1 exit criteria depends on calibration
- [[Plan]] — original plan's emphasis on graded evidence

## Files

- `grade_calls.py` — the grading script
- `graded_calls.csv` — exported results (created by `--csv` flag)
- `research_log/` — the notes being graded
