#!/usr/bin/env python3
"""
run_research_agent_watchlist.py — runs research_agent.py for every ticker
in trader_settings.json's watchlist and sends ONE consolidated Telegram
digest at the end (direction + confidence per ticker), instead of one
notification per ticker — 14 pings for a routine weekly refresh would be
noise, not signal.

This turns "the weekly research refresh" (CLAUDE.md: re-run
research_agent.py on the watchlist weekly) into a single command + one
phone check when it's done, runnable from any session.

Usage:
    python3 run_research_agent_watchlist.py
    python3 run_research_agent_watchlist.py AAPL MSFT   # subset instead of the full watchlist
    python3 run_research_agent_watchlist.py --group Tech  # one watchlist group
    python3 run_research_agent_watchlist.py --list-groups

Detached, survives closing the terminal:
    nohup python3 run_research_agent_watchlist.py > /dev/null 2>&1 & disown
Or: ./run_research_agent_watchlist.sh & disown
"""
import re
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
PER_TICKER_TIMEOUT_S = 15 * 60


def _extract(output: str, header: str) -> str:
    """Pull the first meaningful line out of a '## <header>' section of
    research_agent.py's required note format."""
    m = re.search(rf"##\s*{header}[^\n]*\n(.+?)(?=\n##|\Z)", output, re.DOTALL | re.IGNORECASE)
    if not m:
        return "?"
    for line in m.group(1).splitlines():
        line = line.strip("* \t-")
        if line:
            return line[:120]
    return "?"


def main() -> None:
    args = sys.argv[1:]
    scope = "watchlist"
    sys.path.insert(0, str(ROOT))

    if args and args[0] == "--list-groups":
        import trader_app as ta
        import watchlist as wl
        for name, syms in wl.get_groups(ta.load_settings()).items():
            print(f"{name}: {', '.join(syms) or '(empty)'}")
        return

    if args and args[0] == "--group":
        if len(args) < 2:
            sys.exit("--group needs a group name (see --list-groups)")
        import trader_app as ta
        import watchlist as wl
        groups = wl.get_groups(ta.load_settings())
        name = args[1]
        # Case-insensitive match so "tech" finds "Tech".
        match = next((g for g in groups if g.lower() == name.lower()), None)
        if match is None:
            sys.exit(f"No group named {name!r}. Available: {', '.join(groups) or '(none)'}")
        tickers = list(groups[match])
        if not tickers:
            sys.exit(f"Group {match!r} is empty.")
        scope = f"group {match}"
    elif args:
        tickers = [t.upper() for t in args]
        scope = "subset"
    else:
        import trader_app as ta
        tickers = ta.load_settings()["tickers"]

    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_path = LOG_DIR / f"research_watchlist_{ts}.log"
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable

    send_telegram(f"📓 Research agent run started: {len(tickers)} tickers, {scope} ({ts})")

    results = []
    start = time.time()
    with open(log_path, "w") as log_f:
        for i, t in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {t}...")
            log_f.write(f"\n=== {t} ===\n")
            log_f.flush()
            try:
                proc = subprocess.run(
                    [python, str(ROOT / "research_agent.py"), t],
                    cwd=str(ROOT), capture_output=True, text=True,
                    timeout=PER_TICKER_TIMEOUT_S,
                )
            except subprocess.TimeoutExpired:
                results.append((t, "TIMEOUT", "-"))
                log_f.write(f"[TIMEOUT after {PER_TICKER_TIMEOUT_S}s]\n")
                continue
            output = proc.stdout + proc.stderr
            log_f.write(output)
            log_f.flush()
            if proc.returncode != 0:
                results.append((t, "FAILED", "-"))
                continue
            direction = _extract(output, "Direction")
            confidence = _extract(output, "Confidence")
            results.append((t, direction, confidence))

    elapsed_min = (time.time() - start) / 60
    lines = [f"{t}: {d}  (conf: {c})" for t, d, c in results]
    n_ok = sum(1 for _, d, _ in results if d not in ("FAILED", "TIMEOUT"))
    send_telegram(
        f"✅ Research agent run done — {scope} ({elapsed_min:.1f} min, {n_ok}/{len(tickers)} ok)\n\n"
        + "\n".join(lines)
        + f"\n\nFull notes: research_log/   Log: {log_path.name}"
    )
    print(f"\n[watchlist runner] done, elapsed={elapsed_min:.1f}min, log={log_path}")


if __name__ == "__main__":
    main()
