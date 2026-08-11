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

# CAFFEINATE WAS REMOVED HERE ON 2026-08-11, and it was not removed because
# the sleep problem went away.
#
# It moved. `ftmo_watch.sh` now holds a single `caffeinate -i` assertion for
# the whole 16:30-23:00 Sofia session, which is exactly the window this runner
# fires in. A second assertion per firing would be redundant: the machine is
# already being held awake by the watcher for every moment this script can run.
#
# The original diagnosis stands and is worth keeping. On 2026-08-08, 22
# consecutive firings failed because `pmset` idle-sleep is ONE MINUTE on this
# machine — on AC as well as battery, which the earlier note here got wrong.
# launchd fires inside a ~2-second DarkWake, the process opens a socket, and
# macOS suspends underneath it. The failure wears two different error messages
# depending on which clock wins the race on wake ("did not become ready within
# 45.0s" if sleep lands before connect, "TimeoutError: (5, 'Deferred')" if it
# lands during auth) and NEITHER names sleep, which is why the second one reads
# like a new bug. Do not try to fix that in the session code: the retry in
# `_on_connected` burns its attempts against a TCP connection that died during
# suspend, and longer timeouts make failures slower rather than rarer.
#
# If the watcher is ever disabled, this runner goes back to being exposed and
# the assertion should come back here. That is the one condition under which
# re-adding it is correct.
set -u
cd "$(dirname "$0")" || exit 1

exec .venv/bin/python3 ftmo_runner.py "$@"
