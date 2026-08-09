#!/usr/bin/env python3
"""
run_notify.py — generic "run any project script, text me when it's done"
wrapper. Works from any session (this chat, Claude Code, a plain terminal)
for any one-shot script: backtests (sma_crossover_backtest.py,
orb_backtest.py, strategy_shootout.py, variant_experiments.py,
broad_universe_momentum.py, KronosAI/kronos_backtest.py), a single
research_agent.py ticker run, a backtest, or anything else
that runs once and exits.

NOT for recurring/polling jobs (ftmo_runner.py, launchd interval
jobs) — those get a conditional notify hook fired only on an actual
event, wired directly into the script itself, or this wrapper would text
on every no-op poll (ftmo_runner.py wakes ~20x a day and is a
no-op almost every time). See CLAUDE.md's automation section.

What it sends, via TelegramBot/notify.py:
  - started, immediately
  - stalled, if no new output for STALL_TIMEOUT_S (default 20 min) —
    doesn't kill anything, just alerts
  - done, with the last N lines of output (most scripts in this project
    print their real summary/results table as the last thing before
    exiting, so a tail is usually the actual answer, not a guess — see
    KronosAI/run_kronos_backtest_notify.py for a version with a smarter,
    hand-parsed summary if a script's output ever needs that)
  - FAILED, with exit code + tail, if it exits non-zero

Usage:
    python3 run_notify.py <script_path> [args passed through to the script]

Examples:
    python3 run_notify.py sma_crossover_backtest.py
    python3 run_notify.py orb_backtest.py
    python3 run_notify.py strategy_shootout.py
    python3 run_notify.py KronosAI/kronos_backtest.py --sample-count 5
    python3 run_notify.py research_agent.py AAPL

Always runs with the project's shared .venv, and with cwd set to the
target script's own directory (matters for scripts that assume they're
run from their own folder, e.g. anything under KronosAI/).

Detached, survives closing the terminal:
    nohup python3 run_notify.py <script> [args] > /dev/null 2>&1 &
    disown
Or, shorter: ./run_notify.sh <script> [args...] & disown
"""
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "TelegramBot"))
from notify import send_telegram  # noqa: E402

LOG_DIR = ROOT / "run_logs"
LOG_DIR.mkdir(exist_ok=True)
VENV_PYTHON = ROOT / ".venv" / "bin" / "python3"
STALL_TIMEOUT_S = 20 * 60
TAIL_LINES = 25


def _tail(text: str, n: int = TAIL_LINES) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:]) if lines else "(no output captured)"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 run_notify.py <script_path> [args...]")
    script_arg = sys.argv[1]
    extra_args = sys.argv[2:]

    script_path = Path(script_arg)
    script_path = script_path.resolve() if script_path.is_absolute() else (ROOT / script_arg).resolve()
    if not script_path.exists():
        sys.exit(f"Script not found: {script_path}")

    label = script_path.stem
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = LOG_DIR / f"{label}_{ts}.log"

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [python, str(script_path), *extra_args]
    run_dir = script_path.parent

    send_telegram(
        f"🚀 {label} started ({ts})"
        f"{'  args: ' + ' '.join(extra_args) if extra_args else ''}\n"
        f"Log: {log_path.name}"
    )

    start = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=str(run_dir), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        send_telegram(f"❌ {label} could not even START ({ts})\n\n{e}")
        raise

    output_lines: list[str] = []
    last_output_time = time.time()
    stalled_alert_sent = False

    with open(log_path, "w") as log_f:
        while True:
            line = proc.stdout.readline()
            if line:
                print(line, end="")  # still visible if run in the foreground
                log_f.write(line)
                log_f.flush()
                output_lines.append(line)
                last_output_time = time.time()
            elif proc.poll() is not None:
                break
            else:
                time.sleep(0.5)

            if not stalled_alert_sent and time.time() - last_output_time > STALL_TIMEOUT_S:
                send_telegram(
                    f"⚠️ {label} ({ts}) has produced no output for "
                    f"{STALL_TIMEOUT_S // 60} min — might be hung. Still "
                    f"running, not killed. Log: {log_path.name}"
                )
                stalled_alert_sent = True

    elapsed_min = (time.time() - start) / 60
    full_output = "".join(output_lines)
    returncode = proc.returncode

    if returncode == 0:
        send_telegram(
            f"✅ {label} done ({elapsed_min:.1f} min)\n\n{_tail(full_output)}\n\n"
            f"Full log: {log_path.name}"
        )
    else:
        send_telegram(
            f"❌ {label} FAILED (exit {returncode}, {elapsed_min:.1f} min in)\n\n"
            f"{_tail(full_output)}\n\nFull log: {log_path.name}"
        )

    print(f"\n[wrapper] exit={returncode}  elapsed={elapsed_min:.1f}min  log={log_path}")
    sys.exit(returncode)


if __name__ == "__main__":
    main()
