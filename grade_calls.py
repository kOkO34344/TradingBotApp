#!/usr/bin/env python3
"""
grade_calls.py — turns research_log/ into the evidence that earns (or denies) autonomy.

Reads every research note, extracts the direction call and confidence, fetches
what the price actually did afterward, and grades each call at 5-day and
21-day horizons. Then prints a calibration report: win rate by direction and by
confidence bucket. Run it weekly; the report IS the agent's track record.

Grading rules (deliberately blunt):
  long   -> correct if forward return > +0.5%  (beats noise threshold)
  short  -> correct if forward return < -0.5%
  no-edge-> correct if |forward return| <= 2%  (the market indeed went nowhere)
Notes younger than the horizon are marked pending, not graded.

Usage: python3 grade_calls.py            # grade everything, print report
       python3 grade_calls.py --csv      # also write graded_calls.csv
"""

import argparse
import re
import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

LOG_DIR = Path(__file__).parent / "research_log"
NOISE, FLAT_BAND = 0.5, 2.0  # percent
HORIZONS = {"5d": 5, "21d": 21}


def parse_note(path: Path) -> dict | None:
    m = re.match(r"([A-Z0-9=.\-^]+)_(\d{4}-\d{2}-\d{2})_\d{4}\.md", path.name)
    if not m:
        return None
    ticker, date_s = m.group(1), m.group(2)
    text = path.read_text()

    direction = None
    dir_sec = re.search(r"##\s*Direction[^\n]*\n(.{0,300})", text, re.IGNORECASE | re.DOTALL)
    if dir_sec:
        seg = dir_sec.group(1).lower()
        if re.search(r"no[\s-]?edge", seg):
            direction = "no-edge"
        elif "short" in seg and "long" not in seg.split("short")[0][-20:]:
            direction = "short"
        elif "long" in seg:
            direction = "long"
    conf = None
    conf_sec = re.search(r"##\s*Confidence[^\n]*\n.{0,200}?(\d{1,2})\s*(?:/|\s*out of\s*)?10?",
                         text, re.IGNORECASE | re.DOTALL)
    if conf_sec:
        c = int(conf_sec.group(1))
        conf = c if 1 <= c <= 10 else None

    if direction is None:
        return None
    return {"file": path.name, "ticker": ticker, "date": date_s,
            "direction": direction, "confidence": conf}


def forward_return(ticker: str, date_s: str, days: int) -> float | None:
    start = datetime.strptime(date_s, "%Y-%m-%d")
    end = start + timedelta(days=days * 2 + 10)  # calendar padding for trading days
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if len(df) < days + 1:
        return None  # not enough future data yet -> pending
    return float((df["Close"].iloc[days] / df["Close"].iloc[0] - 1) * 100)


def grade(direction: str, fwd: float) -> bool:
    if direction == "long":
        return fwd > NOISE
    if direction == "short":
        return fwd < -NOISE
    return abs(fwd) <= FLAT_BAND  # no-edge


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args()

    if not LOG_DIR.exists() or not any(LOG_DIR.glob("*.md")):
        sys.exit("No research notes found in research_log/ — run research_agent.py first.")

    rows = []
    for path in sorted(LOG_DIR.glob("*.md")):
        note = parse_note(path)
        if note is None:
            print(f"  skip {path.name}: could not parse direction", file=sys.stderr)
            continue
        for hname, hdays in HORIZONS.items():
            fwd = forward_return(note["ticker"], note["date"], hdays)
            rows.append({**note, "horizon": hname,
                         "fwd_return_pct": None if fwd is None else round(fwd, 2),
                         "correct": None if fwd is None else grade(note["direction"], fwd)})

    if not rows:
        sys.exit("No gradable notes yet (all skipped or log empty). "
                 "Generate real calls with research_agent.py first.")
    df = pd.DataFrame(rows)
    graded = df.dropna(subset=["correct"])
    pending = df[df["correct"].isna()]

    print(f"\n=== CALL GRADING REPORT — {datetime.now():%Y-%m-%d} ===")
    print(f"Notes: {df['file'].nunique()}   graded: {len(graded)}   pending (too recent): {len(pending)}")
    if len(graded):
        for h in HORIZONS:
            g = graded[graded["horizon"] == h]
            if len(g):
                print(f"\n-- {h} horizon: {g['correct'].mean() * 100:.0f}% correct ({int(g['correct'].sum())}/{len(g)})")
                by_dir = g.groupby("direction")["correct"].agg(["mean", "count"])
                for d, r in by_dir.iterrows():
                    print(f"   {d:<8} {r['mean'] * 100:>4.0f}% correct  (n={int(r['count'])})")
        gc = graded.dropna(subset=["confidence"])
        if len(gc):
            gc = gc.copy()
            gc["bucket"] = pd.cut(gc["confidence"], [0, 4, 6, 10],
                                  labels=["low 1-4", "mid 5-6", "high 7-10"])
            print("\n-- Calibration by confidence (all horizons):")
            for b, r in gc.groupby("bucket", observed=True)["correct"].agg(["mean", "count"]).iterrows():
                print(f"   {b:<9} {r['mean'] * 100:>4.0f}% correct  (n={int(r['count'])})")
            print("   [healthy calibration: high > mid > low. If flat or inverted, "
                  "confidence numbers are noise.]")
    print("\nAutonomy bar (from the plan): months of graded calls with healthy calibration, "
          "then paper trading with approval — in that order.")

    if args.csv:
        out = Path(__file__).parent / "graded_calls.csv"
        df.to_csv(out, index=False)
        print(f"\nWrote {out.name}")


if __name__ == "__main__":
    main()
