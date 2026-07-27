---
tags: [research, grading, calibration]
status: "not due yet - notes too fresh, run weekly starting ~2026-07-25"
source: grade_calls.py
---

# Call Grading System

## Overview

The research agent (`research_agent.py`) produces 12 research notes per cycle. The agent's actual track record — not enthusiasm, not plausibility — is measured by grading those notes against what price actually does afterward.

> [!danger] The only 4 "graded calls" this project ever had were FAKE
> Found 2026-07-28. `graded_calls.csv` carried 4 grades from two notes,
> `AAPL_2026-05-15` and `MSFT_2026-06-01`. Both were **synthetic test notes**
> — each file was one line: *"SYNTHETIC TEST NOTE — not a real call. Safe to
> delete this file."* They were deleted in commit `bdee3c8` when real
> research runs started, but the CSV kept their grades, and
> `daily_digest.py` read that file and reported **"4 graded, 0 pending"**
> every morning for days. Fabricated calibration evidence, presented as a
> track record, in the one file that gates autonomy under rule 5.
>
> Overwritten by a real `--csv` run. **The project has zero real graded
> calls and never had any.** Don't put synthetic notes in `research_log/`;
> if you need them for testing, write them to a temp dir. Treat any report
> claiming grades from notes not present in `research_log/` as corrupt.

**Status 2026-07-28: 38 notes, 0 graded, 76 pending** — verified genuine
against the underlying yfinance data, not merely trusted. `forward_return()`
needs `days + 1` bars, so the 5d horizon wants **6** sessions from the note
date, not 5. (Also note 2026-07-24 has no bar for any ticker — trading-day
math over this window is off by one if you assume a normal week.)

| Notes | 5d grades | 21d grades |
|---|---|---|
| 07-20 (1) | needs 1 more session | needs 17 more |
| 07-21 (11) | needs 2 more sessions | needs 18 more |
| 07-23 (12) | needs 4 more sessions | needs 20 more |
| 07-25 (14) | needs 5 more sessions | needs 21 more |

First real 5d grades land **~2026-07-29**, the bulk early August, nothing at
21d until late August.

## How It Works

### 1. Extract Direction & Confidence

`grade_calls.py` reads each `.md` note in `research_log/` and parses:

- **Direction:** "long", "short", or "no-edge" (extracted from ## Direction section)
- **Confidence:** 1–10 (extracted from ## Confidence section)

If a note doesn't have both, it's marked "skip".

### 2. Fetch Forward Returns

For each note, pull the ticker's price on the note's date and N days later (5d and 21d):

```python
price_today = close[0]
price_5d = close[5]
price_21d = close[21]

return_5d_pct = (price_5d / price_today - 1) * 100
return_21d_pct = (price_21d / price_today - 1) * 100
```

If not enough future price data yet (note too recent), mark as "pending".

### 3. Grade Against Threshold

| Direction | 5d threshold | 21d threshold | Why? |
|---|---|---|---|
| **long** | > +0.5% | > +0.5% | Must beat noise (bid/ask bounce, slippage) |
| **short** | < -0.5% | < -0.5% | Symmetrical to long |
| **no-edge** | within ±2% | within ±2% | Market went nowhere, call was right |

**Rationale for thresholds:**
- 0.5% ≈ typical bid/ask spread + 1-day slippage on a liquid stock
- 2% ≈ roughly what "flat market" means for a week or month
- 5-day threshold is faster (more data density for grading)
- 21-day (one month) is the signal's intended horizon

### 4. Calibration by Confidence

Group all graded calls by confidence bucket (1–4 low, 5–6 mid, 7–10 high) and check:

```
Healthy calibration:
  High-confidence: 65–75% correct
  Mid-confidence:  50–60% correct
  Low-confidence:  40–50% correct
```

If all three buckets are at ~55%, the confidence numbers are **noise** — the agent is guessing, not reasoning.

If high is only 52% but low is 60%, the agent is **worse than random** — it's confident about the wrong things.

## Current Status

**Last grading report:** Synthetic data smoke test (old placeholder notes)

**Real notes:** 12 exist in `research_log/`, dated 2026-07-20 to 2026-07-21

**Action required:** `python3 grade_calls.py --csv` → read `graded_calls.csv` → update [[Graded Calls Tracker]] with current calibration

## Usage

### One-time grading

```bash
python3 grade_calls.py
```

Output: calibration report to stdout, by horizon (5d/21d) and confidence bucket.

### Export to CSV (for analysis)

```bash
python3 grade_calls.py --csv
```

Creates `graded_calls.csv`:
```csv
file,ticker,date,direction,confidence,horizon,fwd_return_pct,correct
AAPL_2026-07-20_1430.md,AAPL,2026-07-20,long,7,5d,+1.2,True
AAPL_2026-07-20_1430.md,AAPL,2026-07-20,long,7,21d,-0.3,False
MSFT_2026-07-20_0900.md,MSFT,2026-07-20,no-edge,6,5d,+0.1,True
...
```

### Weekly habit

Every Friday or Monday:
```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 grade_calls.py --csv
# Read the report, update this note or [[Graded Calls Tracker]]
# Commit to git if tracking this way
```

## Interpreting Results

**Report output example:**

```
=== CALL GRADING REPORT — 2026-07-25 ===
Notes: 12   graded: 18   pending (too recent): 6

-- 5d horizon: 61% correct (11/18)
   long       67% correct  (n=9)
   short       0% correct  (n=2)
   no-edge     67% correct  (n=3)

-- 21d horizon: 52% correct (8/15)
   long       50% correct  (n=6)
   short      20% correct  (n=5)
   no-edge     75% correct  (n=4)

-- Calibration by confidence (all horizons):
   low 1-4    55% correct  (n=11)
   mid 5-6    62% correct  (n=13)
   high 7-10  58% correct  (n=12)
   [healthy calibration: high > mid > low...]
```

**Read this as:**

- 12 unique notes, 18 graded (two horizons × 9 notes enough data)
- 5d view is OK (61% long, 0% short is a concern — shorts are hard, but 0% means agent won't call shorts)
- 21d view is lower (52%), which is normal (longer horizons are noisier)
- **Confidence is *inverted* — high-conf (58%) ≈ mid-conf (62%) ≈ low-conf (55%)**
  - This means the confidence numbers are **noise**. The agent isn't actually calibrated — it's confident-wrong at similar rates to uncertain.

## When to Act On Grading

| Observation | Action |
|---|---|
| Long > 65%, Short > 50%, no-edge > 50% | ✅ Agent is useful; keep using it |
| Long < 55%, overall < 50% | ⚠️ Not better than chance; don't trust live |
| High-conf < mid-conf or mid-conf < low-conf | ❌ Confidence is inverted; recalibrate or rebuild agent |
| < 5 data points per bucket | 🟡 Not enough data yet; keep grading weekly |

## Related Notes

- [[Research Agent Workflow]] — how the agent generates notes
- [[Graded Calls Tracker]] — running log of calibration results
- [[Plan]] — Phase 1 exit criteria ("calibrated grading evidence")

## Files

- `grade_calls.py` — the grading script
- `research_log/` — the research notes being graded
- `graded_calls.csv` — exported grading results (created by `--csv` flag)

## Caveats

1. **Bias toward recent winners.** If we're in a strong bull market and the agent's bias is long, it'll have higher long accuracy just from tailwinds. Watch out for this in calibration.

2. **Small sample sizes early.** The first month of grading will have ~12–20 data points per bucket. Wait until you have 30+ per bucket before declaring the agent "broken" or "great."

3. **Horizon matters.** An agent might be good at 21-day calls and bad at 5-day (or vice versa). Track both separately.

4. **Survivor bias in the watchlist.** These 12 tickers are mega-caps that survived to 2026. An agent tested on a live S&P 500 constituent list (with delistings, bankruptcies, etc.) would have different accuracy.

## Next Steps

1. **Run `grade_calls.py --csv` immediately** — the real notes need grading
2. **Make it a weekly ritual** — Sunday evening or Monday morning, 5 minutes
3. **Update [[Graded Calls Tracker]]** with the latest calibration
4. **If calibration is inverted or < 50%, rebuild the prompt** before trusting live orders
