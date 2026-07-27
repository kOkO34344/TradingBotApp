---
tags: [research, grading, tracker, weekly]
status: "Live — update after running grade_calls.py"
last_updated: 2026-07-28
---

# Graded Calls Tracker

Weekly calibration results from `grade_calls.py --csv`. This is the live evidence of whether the research agent is any good.

**Next action:** `.venv/bin/python grade_calls.py --csv` from **~2026-07-29**,
then fill in the latest row below.

> [!danger] Any pre-2026-07-28 numbers in this file are void
> The 4 grades that existed until then came from two **synthetic test
> notes**, not real calls — see [[Call Grading System]]. The project's real
> record is: **0 graded, 76 pending, as of 2026-07-28.**

**Read the first real report against the pending book's shape:** 38 notes,
**74% no-edge** (28/38), 16% long, 11% short, with confidence clustered low
(18 calls at 3/10, none above 6/10). A mostly-no-edge, low-confidence book is
cheap to be "right" about under the ±2% flat band — the headline win rate will
flatter the skill behind it. Judge the long/short calls separately.

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

| Date | Notes Graded | 5d Correct | 5d Sharpe | 21d Correct | 21d Sharpe | High Conf | Mid Conf | Low Conf | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 2026-07-25 | — | — | — | — | — | — | — | — | **OVERDUE** — run `grade_calls.py --csv` this week |

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
