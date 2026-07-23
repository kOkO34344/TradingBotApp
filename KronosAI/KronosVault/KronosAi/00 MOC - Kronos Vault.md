---
tags: [moc, index, kronos]
status: "Live — integrated, unvalidated"
last_updated: 2026-07-23
---

# Kronos — Map of Contents

Central index for the Kronos integration, a sub-project of the main Trading Bot
(`/Users/kaloyanivanov/TradingBotApp`). Kronos is a foundation model for
financial K-line (candlestick) forecasting, vendored from
[shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) on GitHub, models
hosted on Hugging Face under the `NeoQuasar` org.

See the main vault's [[00 MOC - Trading Bot Vault]] for the whole-project index
and [[Kronos Research Agent]] there for how this fits into the bigger picture
(phases, risk rules, evidence-gating philosophy).

## Quick Status

| Item | Status |
|---|---|
| Vendored into `KronosAI/model/` | ✅ Done 2026-07-23 |
| Dependencies installed (`.venv`) | ✅ torch, einops, huggingface_hub, matplotlib, tqdm, safetensors |
| Smoke test (toy CSV) | ✅ Passes — `kronos_smoke_test.py` |
| Wired to real watchlist | ✅ `kronos_watchlist_forecast.py` / `kronos_agent.py` |
| sample_count stability tested | ✅ 1 vs 10 vs 30 — see [[Kronos Integration Log]] |
| Integrated into `trader_app.py` | ✅ Menu item 7, analysis-only |
| Integrated into `paper_trader.py` | ✅ `--signal kronos`, opt-in, momentum stays default |
| Backtested / calibrated | ❌ **Not done yet — still unvalidated** |

## The Vault by topic

- [[Kronos Overview]] — what it is, the model family, how it forecasts, files and status
- [[Kronos Integration Log]] — dated log of what was built and tested this session, with actual numbers

## Key facts to remember

- **Model:** `Kronos-small` (24.7M params) + `Kronos-Tokenizer-base`, max context 512 bars. Loaded via `KronosTokenizer.from_pretrained(...)` / `Kronos.from_pretrained(...)` from Hugging Face — first run downloads, subsequent runs use the local HF cache.
- **Device:** auto-detects `mps` on this Mac (Apple Silicon GPU) via `KronosPredictor`.
- **Not validated:** no backtest, no `grade_calls.py`-style calibration. Treated the same way `trading_agent_service.py` was treated before it — an evaluation candidate, opt-in only, kept out of anything that runs by default.
- **sample_count matters a lot.** `sample_count=1` is a single noisy sample from the model's output distribution — swung individual tickers' predicted return by 5-10 percentage points between runs on identical input. Averaging more paths (`sample_count=10` or `30`) stabilizes this considerably (~3x tighter). Default in code is 10. See [[Kronos Integration Log]] for the actual numbers.
- **Never share `trader_app.py`'s `price_data/` cache for live/fresh fetches.** It's keyed only by ticker filename, not date range — a `force=True` fetch into it silently overwrites the long-history backtest cache. Kronos's live-ranking fetches go into `price_data_live/` instead (the same cache dir `paper_trader.py`'s momentum ranking already uses).

## Maintenance

This vault currently has no automated sync (unlike the main trading-bot vault,
which has `daily_vault_sync.sh` via launchd). Update it manually when Kronos's
code, integration points, or validation status change — especially once a
backtest or calibration run against Kronos actually happens.
