---
tags: [research, agent, workflow]
source: research_agent.py
status: "Live, ungraded"
---

# Research Agent Workflow

> [!note] Unaffected by the venue change (2026-08-09)
> This workflow is venue-independent — it reads yfinance and writes to
> `research_log/`. The only stale detail: post-trade reflections were written
> by `reflect_on_trades.py`, which was removed with IBKR. The FTMO counterpart
> is `ftmo_closes.py`, and it is better positioned — cTrader returns the actual
> closing deal, so a detected close carries the venue's own price and P&L.


## What It Does

`research_agent.py` is a standalone Python script that:

1. Pulls market data (price, volume, technicals, fundamentals) for a watchlist
2. Feeds that data + a structured prompt to Claude
3. Claude reasons through the data and writes a grounded thesis
4. The thesis is saved as a `.md` file in `research_log/`

Output: **one `.md` per ticker per run**, structured with direction, confidence, risks, and specific price levels.

## How To Run

### One-time (single ticker)

```bash
python3 research_agent.py --ticker AAPL
```

Output: `research_log/AAPL_[timestamp].md`

### Full watchlist (all 12 tickers)

```bash
python3 research_agent.py
```

Runs all 12 (AAPL, MSFT, GOOGL, AMZN, JPM, JNJ, PG, XOM, KO, DIS, NVDA, PLTR) sequentially, ~1 min each, 12 min total.

Output: 12 files in `research_log/`, named `{TICKER}_{timestamp}.md`

### Weekly habit

Every Monday morning or after market close:

```bash
cd /Users/kaloyanivanov/TradingBotApp
python3 research_agent.py
# Wait ~15 min for all to complete
python3 grade_calls.py --csv
```

This produces fresh notes + graded accuracy from the prior week's notes.

## What The Output Looks Like

Each note is structured Markdown:

```markdown
# [TICKER] Research Note — 2026-07-20

## Direction
Long

## Confidence
7/10

## Thesis
[Paragraph of reasoning, grounded in the data pulled]

## Key Levels
- Entry: [price]
- Stop: [price]
- Target: [price]

## Risks
- [Risk 1]
- [Risk 2]

## Sources
- [Data source 1]
- [Indicator 1]
```

Real example (spot-check from the 12 generated 2026-07-20/21):
```markdown
# AAPL Research Note — 2026-07-20 14:30

## Direction
Long

## Confidence
7/10

## Thesis
AAPL has broken above the June consolidation (175.50) and is testing 189.50.
Volume on the breakout was good (45M shares), and the RSI is 68 (overbought
but not yet a reversal signal). The 50-day SMA is 178.23, now acting as support.
Fundamentals unchanged (strong earnings, services growth). Mid-term momentum is
good; short-term risk is mean reversion after a 7% move.

## Key Levels
- Entry: 188.00 (break of high)
- Stop: 182.00 (below 50-day SMA)
- Target: 200.00 (resistance cluster)

## Risks
- Mean reversion on strong rallies (typical 2–3% pullback)
- Fed policy risk (if rates stay high, growth names pressure)
- Earnings miss (next earnings ~4 weeks away)

## Sources
- Price/volume: YFinance (daily + 15min)
- Fundamentals: P/E 28.5, market cap $2.9T
- Technical: SMA50 178.23, RSI 68, MACD positive
```

**Spot-check assessment:** Grounded in actual data, specific levels, realistic risks, not forced directional (would have been fine to say "no-edge"). Calibrated confidence.

## Data Sources

The agent uses:

1. **Daily & 15-min price/volume:** Yahoo Finance (`yfinance`)
   - 1-year daily history
   - 5-day 15-min bars (intraday)

2. **Fundamentals:** Yahoo Finance
   - P/E ratio, market cap, debt/equity, revenue growth, margins
   - Dividend yield
   - 52-week range

3. **Technical indicators:** Computed in `indicators.py` (shared with `trader_app.py`)
   - SMA (20, 50, 200)
   - EMA (12, 26)
   - RSI (14)
   - MACD + histogram
   - Bollinger Bands (20, 2)
   - ATR (14)
   - OBV (On-Balance Volume)
   - VWAP

4. **Knowledge base:** Injected into every prompt via `knowledge/` folder
   - Risk management principles
   - Calibration rules
   - Technical analysis frameworks

**Note:** Fundamentals are cached (pulled once per day); price data is fresh every run.

## The Prompt

The prompt is structured to:

1. **Frame the task:** "Write a grounded, calibrated thesis"
2. **Define frameworks:** "Use technical + fundamental context; acknowledge uncertainty"
3. **Provide data:** [DataFrame of price/volume] + [fundamentals dict] + [indicator table]
4. **Inject constraints:** "5–7/10 confidence means edge-case; 8–10 means clear setup"
5. **Ask for output:** "Respond in Markdown: # Direction, ## Confidence, ## Thesis, ..."

The full prompt lives in `research_agent.py` as a template string. It's tuned to:
- Discourage invented price levels (references actual data)
- Encourage "no-edge" calls when the signal is weak
- Require specific risks and reasoning
- Avoid hedging every call ("it could go up or down")

## Calibration Philosophy

The agent is instructed to use confidence scores like this:

- **1–4 (low confidence):** Market is unclear; flip-a-coin territory; don't trade
- **5–6 (mid confidence):** Slight edge visible, but noisy; risky to position large
- **7–8 (high confidence):** Clear technical setup, fundamentals aligned, risk/reward favorable
- **9–10 (very high confidence):** Rare; multiple confirmations, obvious setup, minimal uncertainty

**Reality check:** In a well-calibrated system, 7–10 calls should be right ~70% of the time, not 90% of the time. This is calibration, not optimism.

## Grading

After 5–21 days, `grade_calls.py` reads each note and checks:

```
IF direction = "long" AND price_5d > +0.5%: CORRECT
IF direction = "short" AND price_5d < -0.5%: CORRECT
IF direction = "no-edge" AND price_5d within ±2%: CORRECT
ELSE: INCORRECT
```

Then groups by confidence and plots accuracy curves. Healthy calibration: high-conf > mid-conf > low-conf, all > 50%.

**Update [[Graded Calls Tracker]] after each grading run.**

## Known Limitations

1. **Survivorship bias:** These 12 are mega-caps that survived to 2026. Fair validation would use dynamic S&P 500 constituents.

2. **Bull market data:** Most of the backtest period (2010–2026) is a bull market. Accuracy in a bear market might differ.

3. **No short calls yet:** The 12 tickers have positive momentum; few "short" calls generated. Once market turns, can better test short accuracy.

4. **Doesn't trade itself:** The agent produces opinions, not positions. A separate system (`paper_trader.py`) would turn opinions into actual orders.

5. **No news/sentiment:** Data is technical + fundamental; no Twitter/social sentiment, no news scanning.

## Next Steps

1. **Run weekly:** `python3 research_agent.py` every Mon/Fri
2. **Grade weekly:** `python3 grade_calls.py --csv` and update [[Graded Calls Tracker]]
3. **Use for Phase 3:** Once calibrated, tie the best-confidence calls into `paper_trader.py` approval gate
4. **Refinement:** If calibration is poor, iterate on the prompt (less bullish, more skeptical, different thresholds, etc.)

## Related Notes

- [[Call Grading System]] — how to grade the notes
- [[Graded Calls Tracker]] — live grading results
- [[Research Agent Workflow]] — this note
- [[Phase Milestones Dashboard]] — Phase 1 status
- [[Kronos Research Agent]] — the project's second research agent, added 2026-07-23: a quantitative forecaster (foundation model) rather than an LLM writing a qualitative thesis. Runs alongside this one, not instead of it — both are unvalidated in different ways (this one ungraded, Kronos unbacktested).

## Files

- `research_agent.py` — the script
- `research_log/` — output notes (one `.md` per ticker per run)
- `indicators.py` — shared technical indicator math
- `graded_calls.csv` — grading results
