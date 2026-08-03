#!/usr/bin/env python3
"""
grade_calls.py — turns research_log/ into the evidence that earns (or denies) autonomy.

Reads every research note, extracts the direction call and confidence, fetches
what the price actually did afterward, and grades each call at 5-day and
21-day horizons. Then prints a calibration report: win rate by direction and by
confidence bucket. Run it weekly; the report IS the agent's track record.

Grading rules — a VOLATILITY-SCALED band that partitions the outcome space:
  band   =  0.5 x that ticker's realized sigma of h-day returns, measured on
            history STRICTLY BEFORE the note date (no lookahead)
  long   -> correct if forward return >  +band
  short  -> correct if forward return <  -band
  no-edge-> correct if |forward return| <= band
Notes younger than the horizon are marked pending, not graded.

Why scaled, and why this is not post-hoc metric-tuning (CLAUDE.md rule 4):
the old rule used a FIXED +/-2% flat band at both horizons and for every name.
That is not a measure of the analyst, it is a measure of the box. Established
2026-08-03 from 2y of price history ALONE — independently of any grade, so the
finding reads identically whatever the agent scored:

  - A no-edge call landed inside +/-2% ~42% of the time BY CHANCE at 5d, and
    only ~21% of the time at 21d. One fixed band across horizons that differ 4x
    in length means the 21d report would have printed ~21% and looked like
    catastrophic failure while measuring nothing at all.
  - 5d sigma across the watchlist ranges 2.4% (KO) to 9.2% (PLTR). Under a fixed
    band, "no edge on KO" lands 63% of the time and "no edge on PLTR" 23% — the
    same call, the same confidence, graded on which ticker it was handed.

The legacy fixed-band grade is still computed and reported alongside, so the
change is auditable and no result is silently restated.

ALSO FIXED 2026-08-03: the old thresholds did not partition. NOISE=0.5 vs
FLAT_BAND=2.0 meant a +1% return graded a `long` call correct AND a `no-edge`
call correct at the same time. One band, three mutually exclusive outcomes.

Usage: python3 grade_calls.py            # grade everything, print report
       python3 grade_calls.py --csv      # also write graded_calls.csv
"""

import argparse
import json
import re
import sys
import warnings
from datetime import datetime, timedelta
from math import comb
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

LOG_DIR = Path(__file__).parent / "research_log"
NOISE, FLAT_BAND = 0.5, 2.0  # percent — LEGACY fixed band, reported for audit only
BAND_SIGMAS = 0.5      # band = BAND_SIGMAS x realized sigma of h-day returns
SIGMA_LOOKBACK_Y = 2   # years of pre-note history the sigma is measured on
HORIZONS = {"5d": 5, "21d": 21}

CACHE_FILE = Path(__file__).parent / "grading_cache.json"

_SIGMA_CACHE: dict = {}   # in-process only, holds the return series
_CACHE: dict = {}         # on-disk, holds struck values
_CACHE_DIRTY = False


# --------------------------------------------------------------- struck cache

def load_cache(refresh: bool = False) -> None:
    """A grade, once struck, must stay struck.

    `forward_return` re-downloading from yfinance on every run made the report
    non-deterministic: three consecutive runs on identical notes graded the
    same book 37% / 34% / 37% under the legacy band (2026-08-03). yfinance
    silently returns slightly different bars run to run, and a call sitting
    near its band flips with them.

    That is not survivable for `graded_calls.csv` specifically — it is the
    file the autonomy bar is read from, and this project has already been
    burned once by that CSV asserting a track record that was not real. An
    audit trail that changes when you re-read it is not an audit trail.

    So every value derived from price data is written here the first time it
    resolves and reused verbatim afterwards. Only genuinely PENDING calls
    (not enough forward data yet) are left uncached and retried. `--refresh`
    discards the lot and re-strikes from current data, deliberately explicit.
    """
    global _CACHE
    if refresh or not CACHE_FILE.exists():
        _CACHE = {}
        return
    try:
        _CACHE = json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"  grading cache unreadable ({e}) — re-striking from live data",
              file=sys.stderr)
        _CACHE = {}


def save_cache() -> None:
    if not _CACHE_DIRTY:
        return
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_CACHE, indent=1, sort_keys=True))
    tmp.replace(CACHE_FILE)


def _cached(key: str, compute):
    """Return the struck value for `key`, computing and storing it if absent.
    A computed None means 'not resolvable yet' and is deliberately NOT stored."""
    global _CACHE_DIRTY
    if key in _CACHE:
        return _CACHE[key]
    val = compute()
    if val is not None:
        _CACHE[key] = val
        _CACHE_DIRTY = True
    return val


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
    def fetch():
        start = datetime.strptime(date_s, "%Y-%m-%d")
        end = start + timedelta(days=days * 2 + 10)  # calendar padding for trading days
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
        if df is None or not len(df):
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if len(df) < days + 1:
            return None  # not enough future data yet -> pending
        return float((df["Close"].iloc[days] / df["Close"].iloc[0] - 1) * 100)

    return _cached(f"fwd|{ticker}|{date_s}|{days}", fetch)


def _hist_returns(ticker: str, date_s: str, days: int):
    """Overlapping `days`-bar returns (percent) from history STRICTLY BEFORE
    date_s, or None.

    The cutoff matters: measuring over a window that includes the outcome
    would let the band widen exactly when the call went wrong, which is the
    lookahead version of the bug this whole change exists to fix.
    """
    key = (ticker, date_s, days)
    if key in _SIGMA_CACHE:
        return _SIGMA_CACHE[key]

    end = datetime.strptime(date_s, "%Y-%m-%d")
    start = end - timedelta(days=int(365.25 * SIGMA_LOOKBACK_Y))
    df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
    rets = None
    if df is not None and len(df):
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close = df["Close"].squeeze()
        if len(close) >= days * 4:  # need a usable number of overlapping windows
            r = ((close.shift(-days) / close - 1) * 100).dropna()
            if len(r) and float(r.std()) > 0:
                rets = r
    _SIGMA_CACHE[key] = rets
    return rets


def hist_stats(ticker: str, date_s: str, days: int) -> dict | None:
    """Sigma and the per-direction chance rates, struck once from pre-note
    history. Cached on disk with the grades — the band and the null are as
    much a part of a struck grade as the forward return is, so re-striking
    one without the others would make an old grade unreproducible."""
    def compute():
        rets = _hist_returns(ticker, date_s, days)
        if rets is None:
            return None
        band = BAND_SIGMAS * float(rets.std())
        return {"sigma": float(rets.std()),
                "long": float((rets > band).mean()),
                "short": float((rets < -band).mean()),
                "no-edge": float((rets.abs() <= band).mean())}

    return _cached(f"hist|{ticker}|{date_s}|{days}", compute)


def realized_sigma(ticker: str, date_s: str, days: int) -> float | None:
    st = hist_stats(ticker, date_s, days)
    return None if st is None else st["sigma"]


def null_rate(ticker: str, date_s: str, days: int, direction: str) -> float | None:
    """How often this call would have been correct BY CHANCE — the empirical
    frequency, on pre-note history, of the outcome region the call claims.

    A win rate without its null is not evidence. 37% looks like failure and
    26% looks worse, but if chance is 39% then both are simply noise. Reported
    on every line so no future run can be read as skill without checking.
    """
    st = hist_stats(ticker, date_s, days)
    return None if st is None else st.get(direction)


def band_for(ticker: str, date_s: str, days: int) -> tuple[float, bool]:
    """(band in percent, is_scaled). Falls back to the legacy fixed band when
    there is not enough pre-note history to measure sigma."""
    sigma = realized_sigma(ticker, date_s, days)
    if sigma is None:
        return FLAT_BAND, False
    return BAND_SIGMAS * sigma, True


def grade(direction: str, fwd: float, band: float) -> bool:
    """One band, three mutually exclusive outcomes."""
    if direction == "long":
        return fwd > band
    if direction == "short":
        return fwd < -band
    return abs(fwd) <= band  # no-edge


def binom_two_sided(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial p-value. Hand-rolled: scipy is not a project
    dependency and pulling one in for a single test is not worth the install."""
    if n == 0 or not (0.0 < p < 1.0):
        return 1.0
    pmf = [comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(n + 1)]
    return min(1.0, sum(x for x in pmf if x <= pmf[k] * (1 + 1e-9)))


def grade_legacy(direction: str, fwd: float) -> bool:
    """The old fixed-band rule, kept so the change stays auditable.
    Note the overlap it carried: NOISE < FLAT_BAND meant `long` and `no-edge`
    could both be correct for the same forward return."""
    if direction == "long":
        return fwd > NOISE
    if direction == "short":
        return fwd < -NOISE
    return abs(fwd) <= FLAT_BAND


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="discard the struck-grade cache and re-strike from current "
                         "data. Grades may move — yfinance is not stable run to run.")
    args = ap.parse_args()

    if not LOG_DIR.exists() or not any(LOG_DIR.glob("*.md")):
        sys.exit("No research notes found in research_log/ — run research_agent.py first.")

    load_cache(refresh=args.refresh)
    if args.refresh and CACHE_FILE.exists():
        print("--refresh: re-striking every grade from current data.", file=sys.stderr)
    struck_before = len(_CACHE)

    rows = []
    for path in sorted(LOG_DIR.glob("*.md")):
        note = parse_note(path)
        if note is None:
            print(f"  skip {path.name}: could not parse direction", file=sys.stderr)
            continue
        for hname, hdays in HORIZONS.items():
            fwd = forward_return(note["ticker"], note["date"], hdays)
            band, scaled = band_for(note["ticker"], note["date"], hdays)
            rows.append({**note, "horizon": hname,
                         "fwd_return_pct": None if fwd is None else round(fwd, 2),
                         "band_pct": round(band, 2), "band_scaled": scaled,
                         "null_p": null_rate(note["ticker"], note["date"], hdays,
                                             note["direction"]),
                         "correct": None if fwd is None else grade(note["direction"], fwd, band),
                         "correct_legacy": None if fwd is None
                         else grade_legacy(note["direction"], fwd)})

    if not rows:
        sys.exit("No gradable notes yet (all skipped or log empty). "
                 "Generate real calls with research_agent.py first.")
    df = pd.DataFrame(rows)
    graded = df.dropna(subset=["correct"])
    pending = df[df["correct"].isna()]

    print(f"\n=== CALL GRADING REPORT — {datetime.now():%Y-%m-%d} ===")
    print(f"Notes: {df['file'].nunique()}   graded: {len(graded)}   pending (too recent): {len(pending)}")
    newly = len(_CACHE) - struck_before
    print(f"Struck grades: {len(_CACHE)} cached ({newly} new this run) — "
          f"reproducible, re-strike with --refresh.")
    if len(graded):
        n_unscaled = int((~graded["band_scaled"]).sum())
        print(f"\nBand: {BAND_SIGMAS}x realized sigma per ticker/horizon, measured on "
              f"{SIGMA_LOOKBACK_Y}y of pre-note history."
              + (f"  ({n_unscaled} fell back to the fixed +/-{FLAT_BAND}%)" if n_unscaled else ""))
        for h in HORIZONS:
            g = graded[graded["horizon"] == h]
            if len(g):
                bands = g["band_pct"]
                print(f"\n-- {h} horizon: {g['correct'].mean() * 100:.0f}% correct "
                      f"({int(g['correct'].sum())}/{len(g)})"
                      f"   [legacy fixed band: {g['correct_legacy'].mean() * 100:.0f}%]")
                print(f"   band range {bands.min():.1f}%-{bands.max():.1f}% "
                      f"(median {bands.median():.1f}%)")
                gn = g.dropna(subset=["null_p"])
                if len(gn):
                    a, nul = gn["correct"].mean(), gn["null_p"].mean()
                    pv = binom_two_sided(int(gn["correct"].sum()), len(gn), nul)
                    print(f"   vs CHANCE {nul * 100:.0f}%  ->  edge {(a - nul) * 100:+.0f}pt"
                          f"   (exact binomial p={pv:.2f}"
                          f"{'' if pv <= 0.05 else ', i.e. indistinguishable from guessing'})")
                print(f"   {'direction':<10}{'n':>4}{'actual':>9}{'chance':>9}{'edge':>8}{'legacy':>9}")
                for d, gg in g.groupby("direction"):
                    nul = gg["null_p"].mean()
                    line = (f"   {d:<10}{len(gg):>4}{gg['correct'].mean() * 100:>8.0f}%")
                    line += ("       ?" + " " * 8) if nul != nul else (
                        f"{nul * 100:>8.0f}%{(gg['correct'].mean() - nul) * 100:>+7.0f}pt")
                    print(line + f"{gg['correct_legacy'].mean() * 100:>8.0f}%")
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

    save_cache()

    if args.csv:
        out = Path(__file__).parent / "graded_calls.csv"
        df.to_csv(out, index=False)
        print(f"\nWrote {out.name}")


if __name__ == "__main__":
    main()
