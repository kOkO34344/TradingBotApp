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

# WHY caffeinate. Diagnosed 2026-08-08 after 22 consecutive failed firings.
# `pmset -b sleep` is 1 MINUTE on battery, so launchd wakes the runner inside a
# ~2-second DarkWake and macOS suspends the process mid-handshake. Proven by
# lining pmset -g log up against ftmo_launchd.log: 16:37:36 DarkWake,
# 16:37:37 "trading window", 16:37:38 Sleep, error at 17:07:59 — thirty minutes
# of wall clock in which the process barely ran.
#
# `-i` prevents idle SYSTEM sleep for the lifetime of the child ONLY, so the
# machine stays awake for the ~3 minutes a firing takes and is free to sleep
# again immediately after. No system-wide pmset setting is changed, and an
# out-of-window firing still exits in about a second.
#
# NOT `-s`: that prevents system sleep only on AC power, and this machine runs
# on battery, so it would silently do nothing — which is the worst kind of fix.
#
# This lives in the wrapper rather than the plist deliberately: the wrapper is
# tracked in git and re-read on every firing, so changing it needs no
# `launchctl` reload. This project has documented that launchctl reports a
# different domain from a sandboxed shell and that loaded jobs can look
# unloaded — not having to touch it is worth more than the tidiness.
#
# Degrades to running bare if caffeinate is missing. An unattended runner that
# refuses to start because a convenience tool is absent is a worse failure than
# the one being fixed.
set -u
cd "$(dirname "$0")" || exit 1

if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i .venv/bin/python3 ftmo_runner.py "$@"
fi
exec .venv/bin/python3 ftmo_runner.py "$@"
