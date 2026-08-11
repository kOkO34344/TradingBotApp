#!/bin/bash
# ftmo_watch.sh — wrapper for the continuous equity watcher
# (com.tradingbotapp.ftmowatch). Long-lived: launchd keeps it alive and it is
# expected to run forever, reconnecting on its own.
#
# The venv pin is load-bearing for the same reason it is in ftmo_runner.sh:
# `python3` on this machine resolves to conda base, which is a partial match
# for this project's dependencies. The watcher does not need torch, but it does
# need the cTrader SDK, and a wrapper that silently picks a different
# interpreter is how "it was running" becomes "it was not".
#
# CAFFEINATE IS THE POINT OF THIS WRAPPER (owner decision, 2026-08-11).
#
# `caffeinate -i` takes a PreventUserIdleSystemSleep assertion for the lifetime
# of the child. The watcher exits itself when the 16:30-23:00 Sofia session
# closes, so the assertion is held for the SESSION and released the moment it
# ends — the Mac is free to sleep for the other 17.5 hours and all weekend.
#
# THIS IS THE SLEEP FIX, and it is deliberately not a `pmset` change. This
# machine idle-sleeps after ONE MINUTE on both battery and AC (`pmset -g
# custom`), which is what cost 22 consecutive firings on 2026-08-08 and what
# would otherwise leave an auto-flatten watcher asleep through the exact hours
# it exists to cover. Changing `pmset` would need sudo, would be a system-wide
# setting nobody re-reads, and would apply at 04:00 on a Sunday when nothing
# can trade. An assertion scoped to a process that only lives during market
# hours expresses the actual intent.
#
# WHAT IT COSTS: roughly 20-50Wh per session on a laptop that is usually
# unplugged — a quarter to half a charge. That is a real cost and it was chosen
# knowingly over the alternatives (no assertion at all, or holding one only
# while a position is open). caffeinate does NOT harm battery HEALTH: it
# consumes no CPU and generates no meaningful heat, it only prevents the
# machine dropping into a low-power state. The cost is runtime, not lifespan.
#
# NOT `-s`: that prevents system sleep only on AC power. NOT `-d`: the display
# is free to sleep, which saves most of the power while keeping the CPU alive.
#
# WHAT IT CANNOT DO: closing the lid sleeps the Mac regardless of any
# assertion. If the lid shuts mid-session the watcher stops and launchd starts
# it again on the next open. There is no software fix for that, and pretending
# otherwise would be worse than saying so here.
#
# Degrades to running bare if caffeinate is absent. An unattended process that
# refuses to start because a convenience tool is missing is a worse failure
# than the one being prevented.
set -u
cd "$(dirname "$0")" || exit 1

if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -i .venv/bin/python3 ftmo_watch.py "$@"
fi
exec .venv/bin/python3 ftmo_watch.py "$@"
