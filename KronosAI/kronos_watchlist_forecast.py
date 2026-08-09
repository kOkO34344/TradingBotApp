"""
kronos_watchlist_forecast.py

Ad hoc CLI for eyeballing Kronos forecasts against the real watchlist
(trader_settings.json) — the toy CSV smoke test lives in
kronos_smoke_test.py. Thin wrapper around kronos_agent.forecast_tickers();
see that module's docstring for the sample_count stabilization findings
and how this feeds ftmo_runner.py / trader_app.py.

Usage:
  python3 kronos_watchlist_forecast.py                        forecast all watchlist tickers
  python3 kronos_watchlist_forecast.py AAPL MSFT               forecast just these tickers
  python3 kronos_watchlist_forecast.py --sample-count 20       average 20 sampled paths per ticker
"""
import argparse

from kronos_agent import forecast_tickers, get_predictor, PRED_LEN, DEFAULT_SAMPLE_COUNT
import trader_app as ta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", help="tickers to forecast (default: full watchlist)")
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT,
                         help=f"forecast paths averaged internally per ticker (default: {DEFAULT_SAMPLE_COUNT} — "
                              "sample_count=1 is a single noisy draw, see kronos_agent.py docstring)")
    args = parser.parse_args()

    settings = ta.load_settings()
    tickers = args.tickers or settings["tickers"]

    print("Loading tokenizer + model from Hugging Face Hub...")
    predictor = get_predictor()
    print(f"Predictor ready on device: {predictor.device}")

    ok_tickers, hist_data, pred_dfs = forecast_tickers(
        tickers, pred_len=PRED_LEN, sample_count=args.sample_count, verbose=True)

    print(f"\n{'Ticker':<8}{'Last Close':>12}{'Pred End Close':>16}{'Chg %':>10}")
    for t in ok_tickers:
        last_close = hist_data[t]["Close"].iloc[-1]
        pred_end_close = pred_dfs[t]["close"].iloc[-1]
        chg_pct = (pred_end_close / last_close - 1) * 100
        print(f"{t:<8}{last_close:>12.2f}{pred_end_close:>16.2f}{chg_pct:>9.2f}%")


if __name__ == "__main__":
    main()
