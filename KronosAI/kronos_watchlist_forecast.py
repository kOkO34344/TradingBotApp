"""
kronos_watchlist_forecast.py

Wires the vendored Kronos model to this project's real watchlist (from
trader_settings.json) instead of the toy CSV in kronos_smoke_test.py.
Reuses trader_app.load_settings()/fetch() rather than reimplementing a
data pull.

Always force-refetches through today into its own cache dir, same fix as
paper_trader.py's LIVE_DATA_DIR: sharing trader_app's price_data/ cache
(keyed only by ticker, not date range) silently serves stale/truncated
history to whichever script asks first. See CLAUDE.md / paper_trader.py
comment for the incident this avoids.

Evaluation candidate only: prints raw forecasts for inspection. Not wired
into research_agent.py, trade_journal.csv, or the paper trader — see
CLAUDE.md rule 5 (autonomy earned by evidence, not capability).

sample_count matters: a single sample (sample_count=1) is noisy — one full
watchlist run swung individual tickers by 5-10pp of predicted change vs a
second run. Averaging 10 paths cut that swing roughly 3x; 10 vs 30 agreed
within ~1pp on most tickers (AAPL was the outlier, still ~2.6pp apart at
n=30). Default here is 10 as a speed/stability tradeoff — bump it with
--sample-count for a tighter estimate at the cost of runtime.

Usage:
  python3 kronos_watchlist_forecast.py                        forecast all watchlist tickers
  python3 kronos_watchlist_forecast.py AAPL MSFT               forecast just these tickers
  python3 kronos_watchlist_forecast.py --sample-count 20       average 20 sampled paths per ticker
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
import trader_app as ta

from model import Kronos, KronosTokenizer, KronosPredictor

LOOKBACK = 400   # trading days of context fed to the model (Kronos-small max_context=512)
PRED_LEN = 20    # trading days forecast ahead (~1 month, in step with the momentum rebalance cadence)

LIVE_DATA_DIR = Path(__file__).parent.parent / "price_data_live"
LIVE_DATA_DIR.mkdir(exist_ok=True)


def load_ticker_frame(ticker: str) -> pd.DataFrame:
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=int(LOOKBACK * 1.6))).strftime("%Y-%m-%d")
    df = ta.fetch(ticker, start, today, force=True, cache_dir=LIVE_DATA_DIR)
    df = df.rename(columns=str.lower)
    return df[["open", "high", "low", "close", "volume"]].tail(LOOKBACK)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", help="tickers to forecast (default: full watchlist)")
    parser.add_argument("--sample-count", type=int, default=10,
                         help="forecast paths averaged internally per ticker (default: 10 — "
                              "sample_count=1 is a single noisy draw, see module docstring)")
    args = parser.parse_args()

    settings = ta.load_settings()
    tickers = args.tickers or settings["tickers"]

    print("Loading tokenizer + model from Hugging Face Hub...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    predictor = KronosPredictor(model, tokenizer, max_context=512)
    print(f"Predictor ready on device: {predictor.device}")

    df_list, x_ts_list, y_ts_list, ok_tickers = [], [], [], []
    for t in tickers:
        try:
            df = load_ticker_frame(t)
        except Exception as e:
            print(f"  {t}: failed to fetch ({e})")
            continue
        if len(df) < LOOKBACK:
            print(f"  {t}: only {len(df)} bars, need {LOOKBACK} — skipped")
            continue
        x_timestamp = pd.Series(df.index)
        y_timestamp = pd.Series(pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=PRED_LEN))
        df_list.append(df.reset_index(drop=True))
        x_ts_list.append(x_timestamp)
        y_ts_list.append(y_timestamp)
        ok_tickers.append(t)

    if not df_list:
        raise RuntimeError("No tickers had enough history to forecast.")

    print(f"Forecasting {len(ok_tickers)} tickers, {PRED_LEN} trading days ahead, "
          f"sample_count={args.sample_count}: {ok_tickers}")
    pred_df_list = predictor.predict_batch(
        df_list=df_list,
        x_timestamp_list=x_ts_list,
        y_timestamp_list=y_ts_list,
        pred_len=PRED_LEN,
        T=1.0,
        top_p=0.9,
        sample_count=args.sample_count,
        verbose=True,
    )

    print(f"\n{'Ticker':<8}{'Last Close':>12}{'Pred End Close':>16}{'Chg %':>10}")
    for t, hist_df, pred_df in zip(ok_tickers, df_list, pred_df_list):
        last_close = hist_df["close"].iloc[-1]
        pred_end_close = pred_df["close"].iloc[-1]
        chg_pct = (pred_end_close / last_close - 1) * 100
        print(f"{t:<8}{last_close:>12.2f}{pred_end_close:>16.2f}{chg_pct:>9.2f}%")


if __name__ == "__main__":
    main()
