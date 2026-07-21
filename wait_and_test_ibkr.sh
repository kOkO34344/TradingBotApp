#!/bin/bash
# wait_and_test_ibkr.sh — polls until IB Gateway/TWS's PAPER API port opens,
# then automatically runs the connected smoke test (via ibkr_service.py)
# and logs the result. Safe to run in the background while you finish
# logging into Gateway/TWS and enabling the API in the GUI.
#
# Only checks PAPER ports (7497 TWS / 4002 Gateway) — never live ports,
# matching the project's paper-first rule in ibkr_service.py.
#
# Usage: ./wait_and_test_ibkr.sh [timeout_seconds]
# Output: ibkr_connection_test.log in this folder

set -u
cd "$(dirname "$0")" || exit 1
TIMEOUT="${1:-900}"   # default 15 minutes
LOG="ibkr_connection_test.log"
PORTS=(7497 4002)

echo "=== Waiting for IBKR PAPER API port to open (timeout ${TIMEOUT}s) — $(date '+%Y-%m-%d %H:%M:%S') ===" | tee "$LOG"
elapsed=0
found_port=""
while [ "$elapsed" -lt "$TIMEOUT" ]; do
    for p in "${PORTS[@]}"; do
        if nc -z -G 2 127.0.0.1 "$p" 2>/dev/null; then
            found_port="$p"
            break 2
        fi
    done
    sleep 5
    elapsed=$((elapsed + 5))
    if [ $((elapsed % 30)) -eq 0 ]; then
        echo "  ....  still waiting (${elapsed}s elapsed) — log in + enable API in the GUI" >> "$LOG"
    fi
done

if [ -z "$found_port" ]; then
    echo "TIMEOUT after ${TIMEOUT}s: no PAPER API port opened." | tee -a "$LOG"
    echo "Check: are you logged in on PAPER mode? Is 'Enable ActiveX and Socket Clients' checked" | tee -a "$LOG"
    echo "under Configure -> Settings -> API -> Settings? Correct socket port set (7497/4002)?" | tee -a "$LOG"
    exit 1
fi

echo "Port $found_port is OPEN! Running connected smoke test..." | tee -a "$LOG"
source .venv/bin/activate 2>/dev/null

python3 - "$found_port" <<'PYEOF' 2>&1 | tee -a "$LOG"
import sys
import ibkr_service as ibs

port = int(sys.argv[1])
try:
    ib = ibs.connect(port=port)
    acct = ibs.verify_paper_account(ib)
    print(f"CONNECTED. Paper account verified: {acct}")
    c = ibs.stock("AAPL")
    df = ibs.get_15min_bars(ib, c, duration="1 D")
    n = len(df) if df is not None else 0
    print(f"AAPL 15-min bars received: {n} rows")
    ib.disconnect()
    if n > 0:
        print("SMOKE TEST PASSED")
    else:
        print("SMOKE TEST PARTIAL: connected but no bars returned (check market data subscriptions)")
except Exception as e:
    print(f"SMOKE TEST FAILED: {e}")
PYEOF

echo "=== Done — $(date '+%Y-%m-%d %H:%M:%S'). Full log: $LOG ===" | tee -a "$LOG"
