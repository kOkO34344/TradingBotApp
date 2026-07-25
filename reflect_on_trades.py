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
SNAPSHOT_FILE = REFLECT_DIR / ".position_snapshot.json"
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


# ------------------------------------------------- position-snapshot tier
#
# Why this exists, and why reqExecutions alone is not enough:
#
# IBKR only serves executions for the CURRENT session. Verified 2026-07-25:
# reqExecutions with a 30-day ExecutionFilter returned 0 rows. So any close
# that happens while this script isn't polling that same session — overnight,
# over a weekend, machine asleep — is invisible to the execution tier
# FOREVER, and LOOKBACK_DAYS can't help because the data isn't there to ask
# for.
#
# That is not hypothetical: GOOGL's GTC stop fired 2026-07-23 (gapped through
# at the open, -$422) and this script never saw it. Nothing reached
# trade_journal.csv, which is exactly the state the project's rule 6 ("if it
# isn't in the journal, it didn't happen") exists to prevent.
#
# So: compare ib.positions() against a snapshot from the previous run. A
# position that shrank or vanished closed somehow, whether or not an
# execution record survives. Less precise than a real fill (no exit price, no
# broker-reported P&L) but it cannot silently miss the event.

def load_snapshot() -> dict | None:
    """Previous run's positions, or None if this is the first ever run."""
    if SNAPSHOT_FILE.exists():
        try:
            return json.loads(SNAPSHOT_FILE.read_text())
        except Exception:
            return None  # corrupt state re-seeds rather than crashing the job
    return None


def save_snapshot(positions: dict) -> None:
    REFLECT_DIR.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_text(json.dumps(
        {"taken_at": datetime.now().isoformat(timespec="seconds"), "positions": positions},
        indent=2, sort_keys=True))


def current_positions(ib) -> dict:
    return {p.contract.symbol: {"qty": float(p.position), "avg_cost": float(p.avgCost)}
            for p in ib.positions() if p.position != 0}


def diff_positions(prev: dict, cur: dict) -> list[dict]:
    """Symbols whose absolute position shrank since the previous snapshot.

    Only movement TOWARD zero counts — adding to a position, or flipping to a
    larger opposite one, is not a close.
    """
    closes = []
    for sym, before in prev.items():
        qty_before = float(before.get("qty", 0) or 0)
        qty_after = float(cur.get(sym, {}).get("qty", 0) or 0)
        if qty_before == 0 or abs(qty_after) >= abs(qty_before):
            continue
        closes.append({
            "symbol": sym,
            "qty_before": qty_before,
            "qty_after": qty_after,
            "qty_closed": abs(qty_before) - abs(qty_after),
            "avg_cost": float(before.get("avg_cost", 0) or 0),
            "fully_closed": qty_after == 0,
        })
    return closes


def journal_close(symbol: str, action: str, qty: float, price, detail: str,
                  event: str = "CLOSE_DETECTED") -> None:
    """Record a close in trade_journal.csv.

    Both tiers call this. Before it existed, an autonomously-firing GTC stop
    reached the journal from nowhere: paper_trader.py only journals exits it
    places itself.
    """
    try:
        ibs.journal(event=event, contract=ibs.stock(symbol), action=action,
                    quantity=qty, price=price, status="detected", detail=detail)
    except Exception as e:  # journaling must never take the whole run down
        print(f"  WARNING: could not journal {symbol} close: {e}", file=sys.stderr)


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

        # ---- tier 2: position-snapshot diff (catches what executions lose) ----
        prev = load_snapshot()
        cur = current_positions(ib)
        snapshot_closes = []
        if prev is None:
            # First ever run: seed silently. Everything currently open is the
            # baseline, not a close.
            print(f"Seeding position snapshot with {len(cur)} open position(s) "
                  f"— no history to compare against yet.")
        else:
            handled = {f.contract.symbol for f in new_closes}
            window_start = prev.get("taken_at", "unknown")
            for c in diff_positions(prev.get("positions", {}), cur):
                if c["symbol"] in handled:
                    continue  # tier 1 already has it, with exact fill data
                c["window_start"] = window_start
                snapshot_closes.append(c)

        for c in snapshot_closes:
            sym, qty = c["symbol"], c["qty_closed"]
            kind = "fully closed" if c["fully_closed"] else "reduced"
            print(f"  {sym} {kind}: {c['qty_before']:g} -> {c['qty_after']:g} "
                  f"(no execution record; detected by position diff)")
            detail = (
                f"{kind} between {c['window_start']} and detection. No IBKR execution "
                f"record — reqExecutions serves only the current session, so exit price "
                f"and realized P&L are UNKNOWN here. Entry avg cost {c['avg_cost']:.2f}. "
                f"Detection time is not the event time: a close over a weekend or "
                f"overnight is journaled at the next run."
            )
            if args.dry_run:
                continue
            journal_close(sym, "SELL" if c["qty_before"] > 0 else "BUY",
                          qty, "", detail)
            ibs.send_telegram(
                f"\U0001f4c9 Position {kind}: {sym} {c['qty_before']:g} -> {c['qty_after']:g}\n"
                f"Detected by position diff — no execution record, so exit price and "
                f"P&L are unknown.\nEntry avg cost {c['avg_cost']:.2f}. "
                f"Journaled to trade_journal.csv."
            )

        # Advance the snapshot only AFTER every close above is journaled, and
        # never on a --dry-run. Saving earlier would move the baseline past a
        # close that hadn't been recorded yet — if the process then died, or
        # send_telegram stalled through its retry ladder long enough for
        # launchd to kill the job, that close would be lost permanently. That
        # is precisely the failure mode this tier exists to prevent. Detecting
        # the same close twice is harmless by comparison (a duplicate journal
        # row); losing it is not.
        if not args.dry_run:
            save_snapshot(cur)

        if not new_closes:
            if not snapshot_closes:
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

            # Journal first, and independently of the reflection: a failed
            # agent call must not cost us the record of the close itself.
            journal_close(symbol, side.upper(), shares, price,
                          f"{outcome} realized P&L {pnl:+.2f} USD (commission {commission:.2f}), "
                          f"IBKR execId {f.execution.execId}, fill time {f.execution.time}",
                          event="CLOSE_FILLED")

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
