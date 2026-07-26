#!/usr/bin/env python3
"""
watch_orphaned_run.py — waits for an ALREADY-RUNNING kronos_backtest.py
process (started outside run_kronos_backtest_notify.py, e.g. from a
separate Claude Code / terminal session) to finish, then sends the same
completion/failure Telegram summary the wrapper would have sent, by
tailing its log directly. Doesn't touch the process itself besides
watching it — never kills it.

Reusable any time a backtest gets kicked off outside the notify wrapper
and you still want a phone notification when it's done.

Usage:
    python3 watch_orphaned_run.py <PID> <LOG_PATH>

Detached (survives closing the terminal):
    nohup python3 watch_orphaned_run.py <PID> <LOG_PATH> > /dev/null 2>&1 &
    disown
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "TelegramBot"))
from notify import send_telegram  # noqa: E402
from run_kronos_backtest_notify import _extract_summary, _tail  # noqa: E402

STALL_TIMEOUT_S = 20 * 60
MAX_WATCH_S = 4 * 60 * 60  # give up watching (not killing) after 4 hours


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours — still alive
    return True


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("Usage: python3 watch_orphaned_run.py <PID> <LOG_PATH>")
    pid = int(sys.argv[1])
    log_path = Path(sys.argv[2])

    start = time.time()
    last_size, last_change = -1, time.time()
    stalled_sent = False

    print(f"[watcher] watching PID {pid}, log {log_path}")
    while pid_alive(pid):
        if time.time() - start > MAX_WATCH_S:
            send_telegram(f"⏱️ Gave up watching Kronos backtest PID {pid} after "
                          f"{MAX_WATCH_S // 3600}h — it may still be running, just "
                          f"stopped checking. Log: {log_path}")
            return
        time.sleep(10)
        size = log_path.stat().st_size if log_path.exists() else 0
        if size != last_size:
            last_size, last_change = size, time.time()
        elif not stalled_sent and time.time() - last_change > STALL_TIMEOUT_S:
            send_telegram(f"⚠️ Kronos backtest (PID {pid}, picked up mid-run) — no "
                          f"log output for {STALL_TIMEOUT_S // 60} min, might be "
                          f"hung. Still running, not touching it.")
            stalled_sent = True

    elapsed_min = (time.time() - start) / 60
    output = log_path.read_text() if log_path.exists() else ""

    if "=== Stage 2:" in output:
        summary = _extract_summary(output)
        send_telegram(f"✅ Kronos backtest done (picked up mid-run, watched "
                      f"~{elapsed_min:.0f} min)\n\n{summary}")
    else:
        send_telegram(f"❌ Kronos backtest (PID {pid}, picked up mid-run) ended "
                      f"without completing Stage 2 — likely crashed or was "
                      f"killed. Last output:\n\n{_tail(output)}")
    print(f"[watcher] done, elapsed={elapsed_min:.1f}min")


if __name__ == "__main__":
    main()
