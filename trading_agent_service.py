"""
trading_agent_service.py

Thin wrapper around TauricResearch/TradingAgents (https://github.com/TauricResearch/TradingAgents)
for use inside your own Python backend. Runs the multi-agent analysis
on a ticker and returns Claude's buy/hold/sell decision.

IMPORTANT
---------
- This produces a SIMULATED decision only. TradingAgents does not place
  real trades against a brokerage account. If you want live execution,
  you have to add your own broker integration (e.g. Alpaca, Interactive
  Brokers) on top of this, and that's where real money risk starts.
- Each call spends real Anthropic API tokens (roughly the cost of a
  few normal Claude conversations per ticker, depending on model
  choice and debate rounds). Test on a single ticker before running
  this over a watchlist or on a schedule.
- Setup, one time:
    git clone https://github.com/TauricResearch/TradingAgents.git
    cd TradingAgents
    python3 -m venv .venv && source .venv/bin/activate
    pip install .
    export ANTHROPIC_API_KEY="sk-ant-..."
  (Optional) export ALPHA_VANTAGE_API_KEY="..." for richer market data —
  without it, TradingAgents falls back to yfinance automatically.
- Then import analyze_ticker() from this file wherever you need it.
"""

import os
from datetime import date

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG


def _build_config(
    deep_model: str = "claude-sonnet-5",          # heavy reasoning: bull/bear debate, risk mgmt, portfolio manager
    quick_model: str = "claude-haiku-4-5-20251001",  # lightweight: analyst data summarization
    debate_rounds: int = 1,
) -> dict:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it before running any analysis."
        )

    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = "anthropic"
    config["deep_think_llm"] = deep_model
    config["quick_think_llm"] = quick_model
    config["max_debate_rounds"] = debate_rounds
    config["checkpoint_enabled"] = True  # lets you resume an interrupted run instead of re-paying for it
    return config


def analyze_ticker(ticker: str, as_of: str | None = None, debug: bool = False) -> dict:
    """
    Run one TradingAgents pass on `ticker` for a given date (defaults to
    today) and return the decision object (includes the final call plus
    the analyst/debate reports that led to it).
    """
    as_of = as_of or date.today().isoformat()
    config = _build_config()
    graph = TradingAgentsGraph(debug=debug, config=config)
    _, decision = graph.propagate(ticker, as_of)
    return decision


if __name__ == "__main__":
    # Quick smoke test — costs a small amount of Anthropic API usage.
    result = analyze_ticker("NVDA")
    print(result)
