"""
Smoke test for the vendored Kronos foundation model (github.com/shiyu-coder/Kronos).

Purpose: prove the model loads from Hugging Face and predict() runs end to
end on real K-line data, BEFORE wiring Kronos into any research/trading flow.
Not an evaluation candidate yet, not connected to indicators.py or
research_agent.py conventions — see CLAUDE.md work queue.

Data: KronosAI/data/HK_ali_09988_kline_5min_all.csv, shipped in the upstream
repo (5-min bars, Alibaba HK 09988). Used only because it already has the
required columns; it is unrelated to this project's watchlist.
"""
import pandas as pd
from model import Kronos, KronosTokenizer, KronosPredictor

DATA_PATH = "./data/HK_ali_09988_kline_5min_all.csv"
LOOKBACK = 400
PRED_LEN = 120


def main():
    print("Loading tokenizer + model from Hugging Face Hub...")
    tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")

    predictor = KronosPredictor(model, tokenizer, max_context=512)
    print(f"Predictor ready on device: {predictor.device}")

    df = pd.read_csv(DATA_PATH)
    df["timestamps"] = pd.to_datetime(df["timestamps"])

    x_df = df.loc[:LOOKBACK - 1, ["open", "high", "low", "close", "volume", "amount"]]
    x_timestamp = df.loc[:LOOKBACK - 1, "timestamps"]
    y_timestamp = df.loc[LOOKBACK:LOOKBACK + PRED_LEN - 1, "timestamps"]

    pred_df = predictor.predict(
        df=x_df,
        x_timestamp=x_timestamp,
        y_timestamp=y_timestamp,
        pred_len=PRED_LEN,
        T=1.0,
        top_p=0.9,
        sample_count=1,
    )

    print("Forecasted Data Head:")
    print(pred_df.head())
    print(f"\nSmoke test OK — {len(pred_df)} rows forecasted.")


if __name__ == "__main__":
    main()
