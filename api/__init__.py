"""
api/ — FastAPI backend for the TradingBotApp web UI.

Runs locally only (see CLAUDE.md and web/README.md): it holds a live IB
Gateway connection and can place orders, so it binds to 127.0.0.1 and is
never deployed. The frontend in `web/` talks to it over HTTP + WebSocket.

This package is a thin wrapper, on purpose. Order placement, risk checks,
journalling and sizing all stay in `ibkr_service.py` / `paper_trader.py`
so the browser path and the terminal path cannot diverge in risk handling —
the same reasoning that made `execute_rebalance` shared between the
human-approved and autotrade paths.
"""
