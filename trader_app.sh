#!/bin/bash
# trader_app.sh — launch the interactive trader app under the PROJECT VENV.
#
# Use this instead of `python3 trader_app.py`. On this machine `python3`
# resolves to /opt/anaconda3/bin/python3 (conda base), which has pandas,
# rich, yfinance and ib_async but NOT torch — so the app starts and looks
# completely healthy right up until you open the Kronos menu, which then
# reports "Kronos dependencies not installed: No module named 'torch'".
# The dependencies are installed; they're in .venv, and conda base is a
# different interpreter. A partial environment is a good disguise.
#
# Every automated script in this project already pins .venv/bin/python
# (reflect_on_trades.sh, autotrade_runner.sh, daily_digest.sh); the
# interactive app was the one entry point without a launcher, which is
# exactly why it was the one that broke.
#
# Usage: ./trader_app.sh

set -u
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/python" ]; then
    echo "ERROR: .venv/bin/python not found in $(pwd)."
    echo "Create it with:  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

exec .venv/bin/python trader_app.py "$@"
