#!/bin/bash
# ftmo_runner.sh — invoked by launchd once per FTMO trading day (see
# com.tradingbotapp.ftmo.plist). The "is FTMO autotrade armed" decision is
# made inside ftmo_runner.py, not here — this wrapper just runs it under the
# project venv.
#
# The venv pin is load-bearing. `python3` on this machine resolves to conda
# base, which has pandas and yfinance but NOT torch, so Kronos would fail with
# a missing-module error that reads like a packaging problem and is really the
# wrong interpreter. Every automated script in this project pins .venv the same
# way.
#
# Safe to run manually any time — it no-ops unless ftmo.autotrade.enabled is
# true in trader_settings.json. Arguments pass through, e.g.
#   ./ftmo_runner.sh --force --dry-run

set -u
cd "$(dirname "$0")" || exit 1
.venv/bin/python3 ftmo_runner.py "$@"
