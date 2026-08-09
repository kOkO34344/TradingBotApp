#!/usr/bin/env python3
"""
research_agent.py — Phase 1: research agent, NO execution.

Turns a ticker into a structured, logged investment/trading thesis.
"Chart reading" is done the only way software honestly can: every
indicator the agent reasons about is computed from real price data
(daily AND 15-minute timeframes) and handed to Claude as numbers —
the agent never guesses at what a chart "looks like".

Usage:
    python3 research_agent.py AAPL              # full run (needs Claude auth)
    python3 research_agent.py AAPL --dry-run    # show the prompt, no API call

Auth for full runs: either `export ANTHROPIC_API_KEY=sk-ant-...` or be
logged into Claude Code (the Agent SDK then draws from your Claude
plan's monthly Agent SDK credit instead of separate API billing).

Every run is logged to research_log/<TICKER>_<date>.md so reasoning
quality can be graded over time. That grading — not enthusiasm — is
what eventually earns any autonomy. This script cannot place orders.
"""

import argparse
import asyncio
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

LOG_DIR = Path(__file__).parent / "research_log"
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
KNOWLEDGE_CHAR_CAP = 40_000  # keep the library from crowding out market data
REFLECTIONS_DIR = Path(__file__).parent / "trade_reflections"
REFLECTIONS_CHAR_CAP = 8_000
REFLECTIONS_MAX_NOTES = 5  # most recent only — old trades' lessons matter less than recent ones


def load_knowledge() -> str:
    """Concatenate curated knowledge notes (filename order), capped in size."""
    if not KNOWLEDGE_DIR.exists():
        return ""
    parts = []
    for p in sorted(KNOWLEDGE_DIR.glob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        parts.append(f"--- {p.name} ---\n{p.read_text().strip()}")
    text = "\n\n".join(parts)
    return text[:KNOWLEDGE_CHAR_CAP]


def load_reflections(ticker: str) -> str:
    """Post-trade win/loss self-reviews for this ticker (see ftmo_closes.py),
    most recent first, capped. This is how past outcomes feed back into future
    theses instead of every research run starting from a blank slate."""
    if not REFLECTIONS_DIR.exists():
        return ""
    notes = sorted(REFLECTIONS_DIR.glob(f"{ticker}_*.md"), reverse=True)[:REFLECTIONS_MAX_NOTES]
    if not notes:
        return ""
    parts = [f"--- {p.name} ---\n{p.read_text().strip()}" for p in notes]
    text = "\n\n".join(parts)
    return text[:REFLECTIONS_CHAR_CAP]


# ---------------------------------------------------------------- indicators

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()


def rsi(close: pd.Series, n: int = 14) -> float:
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return float((100 - 100 / (1 + up / dn)).iloc[-1])


def atr(df: pd.DataFrame, n: int = 14) -> float:
    prev_c = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev_c).abs(),
                    (df["Low"] - prev_c).abs()], axis=1).max(axis=1)
    return float(tr.rolling(n).mean().iloc[-1])


def gather_context(ticker: str) -> str:
    """Compute a multi-timeframe technical + fundamental snapshot as text.
    All indicator math comes from indicators.py — the exact numbers the
    owner sees in trader_app.py's chart view (menu 5)."""
    import indicators as ta
    tk = yf.Ticker(ticker)

    daily = _flatten(yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True))
    if len(daily) < 60:
        raise RuntimeError(f"Not enough daily data for {ticker}")
    c = daily["Close"]
    ret = lambda d: float((c.iloc[-1] / c.iloc[-d] - 1) * 100) if len(c) > d else float("nan")

    lines = [
        f"TICKER: {ticker}   generated {datetime.now():%Y-%m-%d %H:%M}",
        "",
        "=== DAILY TIMEFRAME (1 year) ===",
        *ta.summarize_daily(daily),
        f"Returns: 5d {ret(5):+.1f}%   21d {ret(21):+.1f}%   63d {ret(63):+.1f}%   "
        f"1y {float((c.iloc[-1] / c.iloc[0] - 1) * 100):+.1f}%",
        f"20-day realized vol (annualized): {float(c.pct_change().tail(20).std() * np.sqrt(252) * 100):.1f}%",
    ]

    # 15-minute timeframe — the intraday lens
    try:
        m15 = _flatten(yf.download(ticker, period="5d", interval="15m", progress=False, auto_adjust=True))
        if len(m15) < 30:
            raise RuntimeError("not enough intraday bars")
        lines += ["", "=== 15-MINUTE TIMEFRAME (5 days) ===", *ta.summarize_intraday(m15),
                  f"5-day intraday range: {float(m15['Low'].min()):.2f} - {float(m15['High'].max()):.2f}"]
    except Exception as e:
        lines += ["", f"=== 15-MINUTE TIMEFRAME: unavailable ({e}) ==="]

    # Fundamentals (best-effort; yfinance .info is flaky)
    try:
        info = tk.info or {}
        f = lambda k: info.get(k)
        fmt = lambda v, pct=False: ("n/a" if v is None else (f"{v * 100:.1f}%" if pct else f"{v:,.2f}"))
        lines += [
            "",
            "=== FUNDAMENTALS (best-effort) ===",
            f"Market cap: {fmt(f('marketCap'))}   Trailing P/E: {fmt(f('trailingPE'))}   "
            f"Forward P/E: {fmt(f('forwardPE'))}",
            f"Profit margin: {fmt(f('profitMargins'), pct=True)}   Revenue growth: {fmt(f('revenueGrowth'), pct=True)}   "
            f"Debt/equity: {fmt(f('debtToEquity'))}",
            f"Sector: {f('sector') or 'n/a'}   Industry: {f('industry') or 'n/a'}",
        ]
    except Exception:
        lines += ["", "=== FUNDAMENTALS: unavailable ==="]

    return "\n".join(lines)


# ---------------------------------------------------------------- the agent

SYSTEM_FRAME = """You are a senior market analyst writing an internal research note.
Ground every claim in the numbers provided — never invent price levels or data.
Apply these named frameworks explicitly:
1. Multi-timeframe trend alignment (daily SMA/EMA + MACD structure vs 15-minute structure).
2. Momentum vs mean-reversion context (RSI extremes, MACD histogram direction,
   position within Bollinger bands, squeeze conditions).
3. Volume confirmation (OBV trend vs price trend, position vs session VWAP).
4. Structure-aware levels (swing supports/resistances, 52-week range, opening
   range — use these for entries/invalidation, never invented levels).
5. Volatility-aware risk framing (ATR-based stop distance and position sizing implication).
6. Valuation sanity check from the fundamentals, where available.
Be willing to say "no edge here" — a neutral call graded honestly is worth more
than a confident call graded badly. You are writing RESEARCH. You cannot and do
not place trades; nothing you write is an instruction to execute."""

REQUIRED_FORMAT = """Structure the note exactly as:
## Thesis (3-5 sentences)
## Direction & timeframe (long / short / no-edge; swing days-weeks vs intraday)
## Confidence (1-10, with one sentence on why not higher and not lower)
## Key levels (entry zone, invalidation/stop, target — all derived from the data given)
## Risks (top 3, concrete)
## What would change my mind (specific, observable)"""


def build_prompt(ticker: str, context: str) -> str:
    knowledge = load_knowledge()
    kn_block = (f"Curated knowledge base (verified sources — treat as your professional "
                f"training; where it conflicts with hype, the knowledge base wins):\n\n"
                f"{knowledge}\n\n" if knowledge else "")
    reflections = load_reflections(ticker)
    refl_block = (f"Past trade outcomes on {ticker} — honest post-trade reviews from actual "
                  f"closed positions (most recent first). Weigh these: if past theses on this "
                  f"ticker were consistently wrong in a particular way, don't repeat it:\n\n"
                  f"{reflections}\n\n" if reflections else "")
    return f"{SYSTEM_FRAME}\n\n{kn_block}{refl_block}Market data:\n\n{context}\n\n{REQUIRED_FORMAT}"


async def run_agent(prompt: str) -> str:
    from claude_agent_sdk import query, ClaudeAgentOptions
    chunks = []
    async for message in query(prompt=prompt,
                               options=ClaudeAgentOptions(allowed_tools=[])):
        if hasattr(message, "result") and message.result:
            chunks.append(message.result)
    return "\n".join(chunks)


def main():
    ap = argparse.ArgumentParser(description="Phase 1 research agent — analysis only, no execution.")
    ap.add_argument("ticker")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the assembled prompt without calling Claude.")
    args = ap.parse_args()
    ticker = args.ticker.upper()

    print(f"Gathering market context for {ticker}...", file=sys.stderr)
    context = gather_context(ticker)
    prompt = build_prompt(ticker, context)

    if args.dry_run:
        print(prompt)
        return

    try:
        thesis = asyncio.run(run_agent(prompt))
    except ImportError:
        sys.exit("claude-agent-sdk not installed. Run: pip install claude-agent-sdk\n"
                 "(and authenticate: export ANTHROPIC_API_KEY=... or log into Claude Code)")
    if not thesis.strip():
        sys.exit("Empty response — check your Claude authentication (ANTHROPIC_API_KEY "
                 "or Claude Code login) and try again.")

    LOG_DIR.mkdir(exist_ok=True)
    out = LOG_DIR / f"{ticker}_{datetime.now():%Y-%m-%d_%H%M}.md"
    out.write_text(f"# {ticker} research note — {datetime.now():%Y-%m-%d %H:%M}\n\n"
                   f"{thesis}\n\n---\n\n<details><summary>Data the agent saw</summary>\n\n"
                   f"```\n{context}\n```\n</details>\n")
    print(thesis)
    print(f"\n[logged to {out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
