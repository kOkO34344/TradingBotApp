#!/usr/bin/env python3
"""
autotrade_runner.py — unattended hourly rebalancing, no human approval.

Invoked by launchd (com.tradingbotapp.autotrade.plist) roughly hourly
during a window that safely covers NYSE hours; the actual "is the market
open" check happens here in America/New_York time via zoneinfo, not the
host machine's own timezone (this Mac runs EEST/EET, ~7 hours ahead of US
Eastern year-round once both sides have settled into DST or not) — the
launchd schedule only needs to be a superset of real market hours, this
check is authoritative.

EXPERIMENTAL, DELIBERATELY RUN DESPITE A NEGATIVE FINDING: KronosAI's
hourly IC screen (kronos_ic_hourly.py, 2026-07-24) showed NO measurable
edge for either signal at this cadence —
  momentum-hourly: Spearman IC -0.037, 48.5% directional hit rate
  Kronos-hourly:   Spearman IC -0.081, 46.4% directional hit rate
(336 pooled date x ticker pairs, both statistically indistinguishable
from noise). This script runs anyway per the owner's explicit, twice-
confirmed choice to observe it live on the PAPER account — not because
either signal is validated. See CLAUDE.md's empirical findings. Real
money must never run this without new evidence overturning that finding.

Toggle + signal selection live in trader_settings.json's "autotrade"
block, set via trader_app.py's menu (editing the JSON directly works too):
    "autotrade": {"enabled": false, "signal": "kronos"}

Kronos is the project's main signal (owner decision, 2026-07-28). If the
configured signal is a disabled one (momentum), this script REFUSES to fire
— it logs, texts, and places nothing. It never substitutes a different
signal, because "acted=True" in the log with no record of which signal
actually chose the position is worse than not trading. See signal_policy.py.

No-op (exits immediately, no journal entry, no Telegram, one line to
autotrade_runner.log) unless BOTH:
  - autotrade.enabled is true, AND
  - it's a weekday within 9:30-16:00 America/New_York

If both hold: computes the selected hourly signal (autotrade_signals.py),
diffs against current IBKR paper positions, and executes exits-then-
entries through the EXACT same RiskGuard/bracket-order/sizing path
paper_trader.py's human-approved flow uses
(paper_trader.execute_rebalance(..., auto_approve=True)) — no y/n prompt,
every risk_limits.json limit still applies unchanged.

Texts (Telegram) on any executed trade or any error. Stays silent on
no-op cycles (same convention reflect_on_trades.py uses) so this doesn't
spam hourly — every cycle still gets one line in autotrade_runner.log
either way, which is what to check to confirm it's actually alive.

Safety notes:
  - RiskGuard's per-order/position/daily-loss limits are unchanged, but
    they don't guard against slow bleed from turnover costs on a no-edge
    signal trading far more often than the validated monthly cadence.
    trade_journal.csv (cross-referenced with autotrade_runner.log's
    timestamps) is the audit trail for catching that — review it.
  - Paper account only — ibkr_service.connect() refuses live ports/
    accounts regardless of anything in this script.

Usage: normally only invoked by launchd. Safe to run manually to test:
  python3 autotrade_runner.py --force            skip enabled+market-hours checks
  python3 autotrade_runner.py --force --dry-run   same, but print only, no orders
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import ibkr_service as ibs
import signal_policy as sp
import trader_app as ta
from paper_trader import execute_rebalance
# autotrade_signals is NOT imported here — it pulls in torch transitively
# (via KronosAI/kronos_ic_hourly.py), and this script fires hourly whether
# or not autotrade is even enabled. Import it lazily inside main(), only
# after the enabled/market-hours checks pass, so the common case (off, or
# outside market hours) stays a cheap settings-file read, not a torch load.

LOG_PATH = Path(__file__).parent / "autotrade_runner.log"
NY = ZoneInfo("America/New_York")
CLIENT_ID = 13  # distinct from paper_trader.py's (9), trader_app.py's (7), reflect_on_trades.py's (11)


def _log(line: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a") as f:
        f.write(f"[{ts}] {line}\n")
    print(line)


def market_is_open(now_ny: datetime) -> bool:
    """Weekday + 9:30-16:00 America/New_York. No market-holiday calendar —
    a holiday just means this harmlessly attempts against stale/empty data
    and fails gracefully (logged + Telegram-alerted), not a safety issue."""
    if now_ny.weekday() >= 5:  # Saturday/Sunday
        return False
    open_t = now_ny.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now_ny.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now_ny <= close_t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="Skip the autotrade.enabled and market-hours checks (manual testing).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + print the proposal, place no orders.")
    args = ap.parse_args()

    settings = ta.load_settings()
    autotrade = settings.get("autotrade", {})
    now_ny = datetime.now(NY)

    if not args.force:
        if not autotrade.get("enabled", False):
            _log("autotrade disabled — no-op")
            return
        if not market_is_open(now_ny):
            _log(f"market closed ({now_ny.strftime('%Y-%m-%d %H:%M %Z')}) — no-op")
            return

    signal = sp.resolve_signal(settings)
    allow_momentum = sp.momentum_opt_in(settings)

    # Refuse to fire on a disabled signal rather than substituting a different
    # one. Silently trading kronos because momentum was blocked would be the
    # worst outcome: the log would say "acted", and nobody would know which
    # signal actually put the position on.
    try:
        sp.assert_allowed(signal, allow_momentum, context="autotrade_runner")
    except sp.SignalDisabled as e:
        _log(f"REFUSED to fire — configured signal '{signal}' is disabled. No orders placed.")
        ibs.send_telegram(
            f"⛔ autotrade did NOT fire\n"
            f"trader_settings.json's autotrade.signal is '{signal}', which is disabled.\n"
            f"No orders were placed and no other signal was substituted.\n"
            f"Set it to '{sp.DEFAULT_SIGNAL}', or add \"allow_momentum\": true if you "
            f"really want momentum running unattended."
        )
        print(e, file=sys.stderr)
        return

    _log(f"autotrade firing — signal={signal}, force={args.force}, dry_run={args.dry_run}")

    import autotrade_signals as asig  # lazy — see the import comment at the top of this file

    try:
        if signal == "kronos":
            top, data, ranked = asig.compute_live_kronos_hourly(settings)
            signal_label = "kronos-hourly"
        else:
            top, data, ranked = asig.compute_live_momentum_hourly(
                settings, allow_momentum=allow_momentum)
            signal_label = "momentum-hourly"

        top_n = settings.get("momentum_top_n", 3)
        for t in ranked.index:
            marker = "  <= TOP" if t in top else ""
            _log(f"  {t:6s} {ranked[t] * 100:+7.2f}%{marker}")

        port = settings.get("ibkr_port", 4002)
        ib = ibs.connect(port=port, client_id=CLIENT_ID)
        try:
            ibs.verify_paper_account(ib)
            ib.reqMarketDataType(3)  # delayed data — this paper account has no live-data subscription
            did_anything = execute_rebalance(ib, settings, top, data, top_n, signal_label=signal_label,
                                             auto_approve=True, dry_run=args.dry_run)
            _log(f"done — acted={did_anything}")
        finally:
            ib.disconnect()
    except Exception as e:
        _log(f"ERROR: {e}")
        ibs.send_telegram(f"⚠️ autotrade_runner FAILED: {e}")
        raise


if __name__ == "__main__":
    main()
