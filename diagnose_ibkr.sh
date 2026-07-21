#!/bin/bash
# diagnose_ibkr.sh — one-shot health check for the IBKR/TWS API connection.
# Run any time something feels broken: ./diagnose_ibkr.sh
# Exit codes are informational only; this never touches your account.

set -u
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'; NC='\033[0m'
pass() { echo -e "  ${GREEN}PASS${NC}  $1"; }
fail() { echo -e "  ${RED}FAIL${NC}  $1"; }
warn() { echo -e "  ${YELLOW}WARN${NC}  $1"; }

echo "=== IBKR / TWS API Diagnostic — $(date '+%Y-%m-%d %H:%M:%S') ==="

echo -e "\n[1] Is TWS or IB Gateway running?"
if pgrep -f "IB Gateway|Trader Workstation|ibgateway|tws.exe" > /dev/null 2>&1; then
    pass "A TWS/Gateway process is running"
else
    fail "No TWS/Gateway process found — launch it and log into the PAPER account"
fi

echo -e "\n[2] Is TWS/IB Gateway installed?"
FOUND_INSTALL=$(find /Applications "$HOME/Applications" -maxdepth 1 \( -iname "IB Gateway*" -o -iname "Trader Workstation*" \) 2>/dev/null | head -n 1)
if [ -n "$FOUND_INSTALL" ]; then
    pass "Found install: $FOUND_INSTALL"
else
    warn "No install found under the usual /Applications or ~/Applications paths"
fi

echo -e "\n[3] Socket port reachability (127.0.0.1)"
for p in 7497 4002 7496 4001; do
    if nc -z -G 2 127.0.0.1 "$p" 2>/dev/null; then
        label="unknown"
        case $p in
            7497) label="TWS paper" ;;
            4002) label="Gateway paper" ;;
            7496) label="TWS LIVE" ;;
            4001) label="Gateway LIVE" ;;
        esac
        if [ "$p" = "7496" ] || [ "$p" = "4001" ]; then
            warn "port $p ($label) is OPEN — ibkr_service.py refuses this unless allow_live=True is passed explicitly"
        else
            pass "port $p ($label) is OPEN"
        fi
    else
        echo "  ....  port $p closed/unreachable"
    fi
done

echo -e "\n[4] Python environment"
cd "$(dirname "$0")" || exit 1
if [ -d ".venv" ]; then
    pass ".venv exists"
    source .venv/bin/activate
    PYV=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "  ....  python $PYV"
    if python3 -c "import ib_async" 2>/dev/null; then
        VER=$(python3 -c "import ib_async; print(ib_async.__version__)" 2>/dev/null)
        pass "ib_async importable (version ${VER:-unknown})"
    else
        fail "ib_async not importable — run: pip install ib_async"
    fi
else
    fail ".venv not found — run the one-time setup in README_trader_app.md"
fi

echo -e "\n[5] Offline self-test (ibkr_service.py --selftest)"
if [ -f "ibkr_service.py" ]; then
    python3 ibkr_service.py --selftest 2>&1 | tail -n 3
else
    fail "ibkr_service.py not found in $(pwd)"
fi

echo -e "\n[6] Configured port vs settings"
if [ -f "trader_settings.json" ]; then
    PORT=$(python3 -c "import json; print(json.load(open('trader_settings.json')).get('ibkr_port','?'))" 2>/dev/null)
    echo "  ....  trader_settings.json ibkr_port = $PORT"
    if [ "$PORT" = "7497" ]; then
        echo "  ....  7497 = TWS paper port. If you're running IB Gateway instead, change this to 4002."
    elif [ "$PORT" = "4002" ]; then
        echo "  ....  4002 = IB Gateway paper port. If you're running TWS instead, change this to 7497."
    fi
fi

echo -e "\n=== Done ==="
