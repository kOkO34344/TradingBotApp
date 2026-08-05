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
PRED_LEN = 20           # trading days forecast ahead (~1 month, in step with the momentum rebalance cadence)
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
