"""
kronos_agent.py

Kronos as the project's quantitative research agent: forecasts each
watchlist ticker's close price N trading days ahead and ranks the
watchlist by predicted return. Shared by:
  - trader_app.py's "Kronos forecast" menu item (analysis only, no orders)
  - paper_trader.py's `--signal kronos` path (feeds the same approval +
    execution pipeline momentum uses — ATR sizing, bracket orders,
    RiskGuard, trade journal are all untouched by which signal produced
    the ranking)
  - kronos_watchlist_forecast.py, the ad hoc CLI for eyeballing forecasts

forecast_signal() returns the exact (top, data, ranked) shape as
paper_trader.compute_signal() so callers don't need to know which signal
produced it. `data[ticker]` keeps trader_app.fetch()'s original
capitalized OHLCV columns (Open/High/Low/Close/Volume) since that's what
indicators.atr() and paper_trader's sizing expect; only the internal copy
fed to Kronos itself is lower-cased.

Always force-refetches through today into price_data_live/ (same dir
paper_trader.py uses) rather than trader_app's price_data/ — that cache
is keyed only by ticker, not date range, and silently served stale/
truncated history to whichever script asked first (see paper_trader.py's
LIVE_DATA_DIR comment for the incident this avoids).

sample_count matters: a single sample (sample_count=1) is noisy — swung
individual tickers 5-10pp of predicted change between runs. Averaging 10
paths cut that roughly 3x; 10 vs 30 agreed within ~1pp on most tickers.
Default here is 10 as a speed/stability tradeoff.

Status: unvalidated. No backtest, no grade_calls.py-style calibration yet.
Per CLAUDE.md rule 5 (autonomy earned by evidence, not capability), this
stays opt-in — paper_trader.py still defaults to momentum rotation, the
one strategy that's actually earned Phase 3.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
import trader_app as ta

from model import Kronos, KronosTokenizer, KronosPredictor

LOOKBACK = 400          # trading days of context fed to the model (Kronos-small max_context=512)

# Trading days forecast ahead. Owner decision, 2026-08-08: 20 -> 5.
#
# THIS CONSTANT IS LIVE. `ftmo_signal.plan_orders` and `ftmo_runner.run` both
# call `forecast_frames()` WITHOUT passing pred_len, so they inherit whatever
# is set here, and the FTMO runner is armed. Changing this number changes what
# the unattended bot forecasts on its next firing — there is no separate deploy
# step. Pin `pred_len=` at those call sites if the two venues should ever
# diverge.
#
# It was 20 to sit in step with the momentum rebalance cadence, and the whole
# of this project's Kronos evidence was measured at 20: IC 0.036 / 50.0% hit
# rate daily, and the four asset-class screens that all failed. NONE of that
# evidence transfers to 5. The nearest thing we have to a short horizon is the
# hourly screen, where Kronos scored IC -0.081 / 46.4% — its worst result, and
# worse than the momentum baseline on the same screen. Shortening the horizon
# is a thing to TEST, not an improvement that has been demonstrated.
PRED_LEN = 5
DEFAULT_SAMPLE_COUNT = 10

LIVE_DATA_DIR = Path(__file__).parent.parent / "price_data_live"
LIVE_DATA_DIR.mkdir(exist_ok=True)

_predictor = None


def get_predictor() -> KronosPredictor:
    """Loads once per process (Hugging Face download + weight load is the
    slow part); every caller in this process reuses the same instance."""
    global _predictor
    if _predictor is None:
        tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
        model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
        _predictor = KronosPredictor(model, tokenizer, max_context=512)
    return _predictor


def _fetch_fresh(ticker: str) -> pd.DataFrame:
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=int(LOOKBACK * 1.6))).strftime("%Y-%m-%d")
    return ta.fetch(ticker, start, today, force=True, cache_dir=LIVE_DATA_DIR)


def forecast_tickers(tickers: list, pred_len: int = PRED_LEN,
                      sample_count: int = DEFAULT_SAMPLE_COUNT, verbose: bool = True):
    """Fresh force-refetch + Kronos batch forecast for `tickers`.

    Returns (ok_tickers, hist_data, pred_dfs):
      - ok_tickers: subset of `tickers` that had enough history
      - hist_data[t]: raw OHLCV DataFrame, capitalized columns (as fetched)
      - pred_dfs[t]: Kronos's forecast DataFrame (lowercase columns:
        open/high/low/close/volume/amount), indexed by forecast timestamp
    """
    predictor = get_predictor()

    hist_data, df_list, x_ts_list, y_ts_list, ok_tickers = {}, [], [], [], []
    for t in tickers:
        try:
            df = _fetch_fresh(t)
        except Exception as e:
            print(f"  {t}: failed to fetch ({e})", file=sys.stderr)
            continue
        if len(df) < LOOKBACK:
            print(f"  {t}: only {len(df)} bars, need {LOOKBACK} — skipped", file=sys.stderr)
            continue
        kdf = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].tail(LOOKBACK)
        x_ts_list.append(pd.Series(kdf.index))
        y_ts_list.append(pd.Series(pd.bdate_range(kdf.index[-1] + pd.Timedelta(days=1), periods=pred_len)))
        df_list.append(kdf.reset_index(drop=True))
        hist_data[t] = df
        ok_tickers.append(t)

    if not df_list:
        raise RuntimeError("No tickers had enough history to forecast.")

    pred_dfs = _predict(ok_tickers, df_list, x_ts_list, y_ts_list,
                        pred_len, sample_count, verbose)
    return ok_tickers, hist_data, pred_dfs


def forecast_frames(frames: dict, pred_len: int = PRED_LEN,
                    sample_count: int = DEFAULT_SAMPLE_COUNT,
                    verbose: bool = True):
    """Forecast from ALREADY-FETCHED OHLCV frames, skipping yfinance entirely.

    `forecast_tickers()` above is hardwired to `_fetch_fresh()`, i.e. yfinance
    via `ta.fetch`. That is fine for US equities and cannot serve the FTMO
    venue at all: its instruments are CFDs named `US30.cash`, `XAUUSD`,
    `NATGAS.cash` with no yfinance ticker, and forecasting a proxy series
    instead of the one actually being traded would quietly measure the wrong
    thing. `ftmo_signal.py` pulls cTrader trendbars and hands them here.

    `frames` maps symbol -> DataFrame with a DatetimeIndex and OHLCV columns
    (any capitalisation). Returns the same shape `forecast_tickers` does, so
    downstream ranking code is identical for both venues.
    """
    predictor = get_predictor()
    hist_data, df_list, x_ts_list, y_ts_list, ok = {}, [], [], [], []
    for sym, df in frames.items():
        if df is None or len(df) < LOOKBACK:
            print(f"  {sym}: only {0 if df is None else len(df)} bars, "
                  f"need {LOOKBACK} — skipped", file=sys.stderr)
            continue
        kdf = (df.rename(columns=str.lower)[["open", "high", "low", "close",
                                             "volume"]].tail(LOOKBACK))
        x_ts_list.append(pd.Series(kdf.index))
        y_ts_list.append(pd.Series(pd.bdate_range(
            kdf.index[-1] + pd.Timedelta(days=1), periods=pred_len)))
        df_list.append(kdf.reset_index(drop=True))
        hist_data[sym] = df
        ok.append(sym)

    if not df_list:
        raise RuntimeError("No symbols had enough history to forecast.")

    pred_dfs = _predict(ok, df_list, x_ts_list, y_ts_list, pred_len,
                        sample_count, verbose)
    return ok, hist_data, pred_dfs


def _predict(names, df_list, x_ts_list, y_ts_list, pred_len, sample_count,
             verbose):
    """The Kronos call itself. Shared so the yfinance path and the venue-bars
    path cannot drift in temperature, top_p or sampling."""
    if verbose:
        print(f"Forecasting {len(names)} symbols, {pred_len} periods ahead, "
              f"sample_count={sample_count}: {names}")
    pred_df_list = get_predictor().predict_batch(
        df_list=df_list,
        x_timestamp_list=x_ts_list,
        y_timestamp_list=y_ts_list,
        pred_len=pred_len,
        T=1.0,
        top_p=0.9,
        sample_count=sample_count,
        verbose=verbose,
    )
    return dict(zip(names, pred_df_list))


def forecast_signal(settings: dict, pred_len: int = PRED_LEN,
                     sample_count: int = DEFAULT_SAMPLE_COUNT, verbose: bool = False):
    """Same (top, data, ranked) contract as paper_trader.compute_signal():
    ranked = predicted % change in close, `pred_len` trading days out,
    sorted descending; top = top-N tickers (dual-momentum-style "only
    buy positive" filter applied if settings['risk_engine'] is set)."""
    top_n = settings.get("momentum_top_n", 3)
    dual = settings.get("risk_engine", False)
    tickers = settings["tickers"]

    ok_tickers, hist_data, pred_dfs = forecast_tickers(tickers, pred_len, sample_count, verbose)
    if len(ok_tickers) < top_n + 1:
        raise RuntimeError(f"Only {len(ok_tickers)}/{len(tickers)} tickers forecastable — "
                            f"need at least {top_n + 1}.")

    chg = {t: pred_dfs[t]["close"].iloc[-1] / hist_data[t]["Close"].iloc[-1] - 1 for t in ok_tickers}
    ranked = pd.Series(chg).sort_values(ascending=False)
    top = list(ranked.index[:top_n])
    if dual:
        top = [t for t in top if ranked[t] > 0]
    return top, hist_data, ranked


# ------------------------------------------------------------------ selftest

def _selftest() -> int:
    """Offline. Verifies the context/horizon contract without loading weights.

    Stubs `_predict` so nothing downloads from Hugging Face and no GPU work
    happens: what is under test is the framing — how much history goes in and
    how many periods come out — not the model.
    """
    import numpy as np
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    captured = {}

    def fake_predict(names, df_list, x_ts_list, y_ts_list, pred_len,
                     sample_count, verbose):
        captured.update(names=names, df_list=df_list, x_ts_list=x_ts_list,
                        y_ts_list=y_ts_list, pred_len=pred_len)
        # Mirrors the real `_predict`'s return shape (name -> frame). Returning
        # a bare list here passed every framing check and then blew up on the
        # caller's dict lookup — a stub that lies about its contract tests the
        # wrong thing.
        return {n: pd.DataFrame({"close": np.linspace(100, 105, pred_len)})
                for n in names}

    real_predict, real_get = _predict, get_predictor
    globals()["_predict"] = fake_predict
    globals()["get_predictor"] = lambda: None
    try:
        idx = pd.bdate_range("2023-01-02", periods=500)
        frame = pd.DataFrame(
            {"Open": np.linspace(90, 110, 500), "High": np.linspace(91, 111, 500),
             "Low": np.linspace(89, 109, 500), "Close": np.linspace(90, 110, 500),
             "Volume": np.full(500, 1_000_000)}, index=idx)

        print("the horizon is 5 trading days, the context is still 400 bars:")
        check("PRED_LEN is 5", PRED_LEN == 5)
        check("LOOKBACK is unchanged at 400", LOOKBACK == 400)

        ok, hist, preds = forecast_frames({"TEST": frame}, verbose=False)
        check("the symbol was accepted", ok == ["TEST"])
        check("exactly 400 bars of context are fed in, not 500",
              len(captured["df_list"][0]) == 400)
        check("...and they are the MOST RECENT 400",
              captured["x_ts_list"][0].iloc[-1] == idx[-1])
        check("the model is asked for 5 periods", captured["pred_len"] == 5)
        check("exactly 5 forecast timestamps are constructed",
              len(captured["y_ts_list"][0]) == 5)
        check("the forecast starts AFTER the last historical bar",
              captured["y_ts_list"][0].iloc[0] > idx[-1])
        check("forecast timestamps are business days",
              all(ts.weekday() < 5 for ts in captured["y_ts_list"][0]))
        check("the returned forecast frame has 5 rows",
              len(preds["TEST"]) == 5)
        check("history is returned with its original capitalised columns",
              "Close" in hist["TEST"].columns)

        print("a symbol with too little history is skipped, not truncated:")
        short = frame.tail(399)
        raised = False
        try:
            forecast_frames({"SHORT": short}, verbose=False)
        except RuntimeError:
            raised = True
        check("399 bars is refused — 400 means 400", raised)

        print("the live callers inherit this constant (they pass no pred_len):")
        import inspect
        for mod in ("ftmo_signal", "ftmo_runner"):
            src = (Path(__file__).parent.parent / f"{mod}.py").read_text()
            call = src[src.index("forecast_frames("):][:120]
            check(f"{mod} does not pin pred_len, so it follows PRED_LEN",
                  "pred_len" not in call)
    finally:
        globals()["_predict"] = real_predict
        globals()["get_predictor"] = real_get

    print("\nFAILED" if failures else "\nAll kronos_agent offline selftests passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    import argparse
    _ap = argparse.ArgumentParser(description="Kronos research agent.")
    _ap.add_argument("--selftest", action="store_true",
                     help="offline checks; no model download, no network")
    if _ap.parse_args().selftest:
        sys.exit(_selftest())
    _ap.print_help()
