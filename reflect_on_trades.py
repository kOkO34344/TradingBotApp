#!/usr/bin/env python3
"""
reflect_on_trades.py — post-trade self-review, triggered by position closes.

Meant to run periodically (see reflect_on_trades.sh + the launchd job) and
catch EVERY way a position can close: a paper_trader.py rotation exit, or a
GTC stop-loss / target filling on its own with nothing running (the case
the 2026-07-21 DAY-TIF bug taught us to take seriously — see CLAUDE.md).

For each newly-closed position (detected via IBKR's own realized-P&L on the
closing fill, not by re-deriving entry/exit ourselves), spawns a headless
Claude Code agent to answer one question honestly:
  - profit -> "Why are we winning?"
  - loss   -> "Why are we losing — what are we doing wrong with the strategy?"

Output goes to trade_reflections/<SYMBOL>_<date>_<win|loss>.md. That
directory is a first-class input research_agent.py also reads (see
load_reflections() there) — the point is a feedback loop, not a journal
nobody reads.

This script places no orders and connects read-only. Safe to run any time;
if IB Gateway isn't up it just logs a connection error and exits non-zero
for the next scheduled run to retry.

Usage:
  python3 reflect_on_trades.py             # normal run
  python3 reflect_on_trades.py --dry-run   # show what would be reflected on, no agent calls
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import ibkr_service as ibs
from ib_async import ExecutionFilter

BASE_DIR = Path(__file__).parent
REFLECT_DIR = BASE_DIR / "trade_reflections"
STATE_FILE = REFLECT_DIR / ".reflected_execids.json"
RESEARCH_LOG_DIR = BASE_DIR / "research_log"
JOURNAL_FILE = BASE_DIR / "trade_journal.csv"

CLIENT_ID = 11  # distinct from paper_trader.py's (9) so both can run concurrently
LOOKBACK_DAYS = 3  # ExecutionFilter window; dedup via STATE_FILE handles overlap
PNL_SENTINEL_ABS = 1_000_000  # IBKR uses a huge sentinel float for "not applicable"


def load_reflected_ids() -> set:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_reflected_ids(ids: set) -> None:
    REFLECT_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def latest_research_note(symbol: str) -> str:
    notes = sorted(RESEARCH_LOG_DIR.glob(f"{symbol}_*.md"))
    if not notes:
        return ""
    return notes[-1].read_text()[:6000]


def recent_journal_rows(symbol: str, limit: int = 8) -> str:
    if not JOURNAL_FILE.exists():
        return ""
    lines = JOURNAL_FILE.read_text().splitlines()
    header, rows = lines[0], lines[1:]
    matches = [r for r in rows if f",{symbol}," in r]
    return "\n".join([header] + matches[-limit:])


def build_prompt(symbol: str, side: str, shares: float, exit_price: float,
                  realized_pnl: float, commission: float, fill_time: str) -> str:
    is_win = realized_pnl > 0
    thesis = latest_research_note(symbol)
    journal_rows = recent_journal_rows(symbol)
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = f"trade_reflections/{symbol}_{date_str}_{'win' if is_win else 'loss'}.md"

    question = ("Why are we winning?" if is_win else
                "Why are we losing — what are we doing wrong with the strategy?")
    honesty_note = ("Do not credit the strategy for luck. If this looks like variance rather "
                     "than a repeatable edge, say so." if is_win else
                     "Per this project's rule: negative results get reported, not massaged. "
                     "If the entry thesis was simply wrong, say so plainly. If the strategy rules "
                     "did what they were supposed to do and this is normal losing-trade variance "
                     "within an edge that still holds, say that too — don't manufacture a flaw "
                     "that isn't there.")

    return f"""You are reviewing one closed paper-trading position for a systematic trading
project. This is post-trade research, not a trading decision — you cannot and must not place
any order. Answer the question below, honestly, using only the evidence given.

Question: {question}

{honesty_note}

Closed trade:
  Symbol: {symbol}
  Closing side: {side} {shares} shares @ {exit_price}
  Realized P&L: ${realized_pnl:,.2f} (commission ${commission:,.2f})
  Fill time: {fill_time}

Original research thesis for this ticker (most recent note before/around this trade, if any):
---
{thesis or "(no research_log note found for this ticker)"}
---

Recent trade_journal.csv entries for this symbol:
---
{journal_rows or "(no journal rows found)"}
---

Structure your answer exactly as:
## What happened (2-3 sentences, factual)
## {question}
## Was the original thesis right, wrong, or right-for-the-wrong-reasons?
## One concrete takeaway for future trades on this ticker or this strategy

Write your complete answer to the file `{out_path}` using the Write tool. Do not write
anywhere else. Do not modify any other file."""


def run_agent(prompt: str) -> bool:
    result = subprocess.run(
        ["claude", "-p", prompt, "--permission-mode", "acceptEdits",
         "--allowedTools", "Write"],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        print(f"  agent call failed: {result.stderr[:500]}", file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser(description="Reflect on newly-closed paper-trading positions.")
    ap.add_argument("--dry-run", action="store_true",
                     help="Show what would be reflected on, call no agent.")
    args = ap.parse_args()

    REFLECT_DIR.mkdir(exist_ok=True)
    reflected = load_reflected_ids()

    settings_port = 4002
    try:
        import trader_app as ta
        settings_port = ta.load_settings().get("ibkr_port", 4002)
    except Exception:
        pass

    print(f"Connecting to IBKR paper on port {settings_port} (read-only)...")
    ib = ibs.connect(port=settings_port, client_id=CLIENT_ID, readonly=True)
    try:
        ibs.verify_paper_account(ib)

        filt = ExecutionFilter()
        filt.time = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d-%H:%M:%S")
        fills = ib.reqExecutions(filt)

        new_closes = []
        for f in fills:
            exec_id = f.execution.execId
            if exec_id in reflected:
                continue
            pnl = f.commissionReport.realizedPNL if f.commissionReport else 0.0
            if pnl == 0.0 or abs(pnl) >= PNL_SENTINEL_ABS:
                continue  # opening fill or no realized P&L attached
            new_closes.append(f)

        if not new_closes:
            print("No newly-closed positions since last run.")
            return

        print(f"{len(new_closes)} newly-closed position(s) to reflect on.")
        for f in new_closes:
            symbol = f.contract.symbol
            side = f.execution.side
            shares = f.execution.shares
            price = f.execution.avgPrice or f.execution.price
            pnl = f.commissionReport.realizedPNL
            commission = f.commissionReport.commission
            outcome = "WIN" if pnl > 0 else "LOSS"
            print(f"  {symbol} {side} {shares} @ {price} -> {outcome} ${pnl:,.2f}")

            if args.dry_run:
                continue

            prompt = build_prompt(symbol, side, shares, price, pnl, commission, f.execution.time)
            emoji = "\U0001f4b0" if pnl > 0 else "\U0001f4c9"
            if run_agent(prompt):
                reflected.add(f.execution.execId)
                save_reflected_ids(reflected)
                ibs.send_telegram(
                    f"{emoji} Position closed: {symbol} {side} {shares} @ {price:.2f}\n"
                    f"Realized P&L: ${pnl:,.2f}  ({outcome})\n"
                    f"Reflection written to trade_reflections/"
                )
            else:
                print(f"  WARNING: reflection failed for {symbol}, will retry next run.")
                ibs.send_telegram(
                    f"\u26a0\ufe0f Position closed but reflection FAILED: {symbol} {side} "
                    f"{shares} @ {price:.2f}, P&L ${pnl:,.2f}. Will retry next run."
                )
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
