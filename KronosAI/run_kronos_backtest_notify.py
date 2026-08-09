#!/usr/bin/env python3
"""
run_kronos_backtest_notify.py — runs kronos_backtest.py end to end and
pushes a Telegram notification to Koko's phone when it's done, or as soon
as something goes wrong.

Sends:
  - a "started" message immediately
  - a "stalled" alert if the backtest produces no output for
    STALL_TIMEOUT_S (default 20 min) — catches a hung download, a frozen
    predict_batch call, etc. Does NOT kill the process; just alerts, since
    a slow-but-alive run shouldn't be interrupted.
  - a "done" message with the actual Stage 1 (IC/hit rate) and Stage 2
    (Kronos vs momentum vs SPY) numbers pulled out of the run's output
  - a "FAILED" message with the tail of the log if the process exits
    non-zero, including a traceback if kronos_backtest.py raised

Why a separate wrapper instead of editing kronos_backtest.py directly:
keeps that script notification-agnostic (per CLAUDE.md rule 4, it's an
honest-backtest script, not a service) and keeps "what counts as
done/failed/stalled" in one obvious place.

Usage (from KronosAI/):
    python3 run_kronos_backtest_notify.py
    python3 run_kronos_backtest_notify.py --sample-count 5 --seed 7
    (any args are passed straight through to kronos_backtest.py)

Or via the shell wrapper (same pattern as ftmo_runner.sh):
    ./run_kronos_backtest_notify.sh
    ./run_kronos_backtest_notify.sh --sample-count 5

Full output is always saved to backtest_logs/kronos_backtest_<timestamp>.log
regardless of whether Telegram is configured yet.
"""
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "TelegramBot"))
from notify import send_telegram  # noqa: E402

HERE = Path(__file__).parent
LOG_DIR = HERE / "backtest_logs"
LOG_DIR.mkdir(exist_ok=True)
VENV_PYTHON = HERE.parent / ".venv" / "bin" / "python3"
STALL_TIMEOUT_S = 20 * 60  # no new output for this long -> alert (not kill)


def _tail(text: str, n_lines: int = 25) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n_lines:]) if lines else "(no output captured)"


def _extract_summary(output: str) -> str:
    """Pull the two things that actually matter out of kronos_backtest.py's
    stdout: the Stage 1 IC/hit-rate line and the full Stage 2 comparison
    table, rather than texting the whole log."""
    ic_match = re.search(r"Spearman IC[^:]*:\s*([-\d.]+)", output)
    hit_match = re.search(r"Directional hit rate:\s*([\d.]+%)", output)
    table_match = re.search(r"(=== Stage 2:.*)", output, re.DOTALL)

    parts = []
    if ic_match:
        line = f"Spearman IC: {ic_match.group(1)}"
        if hit_match:
            line += f"   hit rate: {hit_match.group(1)}"
        parts.append(line)
    if table_match:
        parts.append(table_match.group(1).strip())

    return "\n\n".join(parts) if parts else "(couldn't parse a summary — check the full log)"


def main() -> None:
    args = sys.argv[1:]
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = LOG_DIR / f"kronos_backtest_{ts}.log"

    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cmd = [python, str(HERE / "kronos_backtest.py"), *args]

    send_telegram(
        f"🚀 Kronos backtest started ({ts})"
        f"{'  args: ' + ' '.join(args) if args else ''}\n"
        f"Log: {log_path.name}"
    )

    start = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=str(HERE), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
    except Exception as e:
        send_telegram(f"❌ Kronos backtest could not even START ({ts})\n\n{e}")
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
                    f"⚠️ Kronos backtest ({ts}) has produced no output for "
                    f"{STALL_TIMEOUT_S // 60} min — might be hung (stuck "
                    f"download, frozen predict_batch, etc). Still running, "
                    f"not killed. Log: {log_path.name}"
                )
                stalled_alert_sent = True

    elapsed_min = (time.time() - start) / 60
    full_output = "".join(output_lines)
    returncode = proc.returncode

    if returncode == 0:
        summary = _extract_summary(full_output)
        send_telegram(
            f"✅ Kronos backtest done ({elapsed_min:.1f} min)\n\n{summary}\n\n"
            f"Full log: {log_path.name}"
        )
    else:
        send_telegram(
            f"❌ Kronos backtest FAILED (exit code {returncode}, "
            f"{elapsed_min:.1f} min in)\n\nLast output:\n{_tail(full_output)}\n\n"
            f"Full log: {log_path.name}"
        )

    print(f"\n[wrapper] exit={returncode}  elapsed={elapsed_min:.1f}min  log={log_path}")
    sys.exit(returncode)


if __name__ == "__main__":
    main()
