#!/usr/bin/env python3
"""
ftmo_smoke_order.py — prove the FTMO order path end to end with ONE tiny trade.

This exists to answer the single question that cannot be answered read-only:
**does a server-side stop actually attach at entry, as every other module
assumes?** `ftmo_sizing` sizes to a stop, `ftmo_rules` budgets against a
simultaneous stop-out across the book, and `ftmo_monitor` decides when to
flatten — all three are worthless if the stop is not really at the venue. The
whole risk model rests on it, and until a real order has been placed and read
back, it is an assumption.

WHAT IT DOES, IN ORDER
  1. Connects and refuses to continue unless the account is flat.
  2. Places ONE order at the symbol's MINIMUM volume with a stop attached.
  3. Reads the position back from the venue and checks the stop is really
     there and really where we asked for it.
  4. Closes it and verifies flat again.

SAFETY RAILS, AND WHY EACH ONE
  - `--confirm` is required. A file that places live orders must not do so
    because someone ran it to see what it did.
  - Minimum volume only. There is no size argument, because the point is the
    mechanism and not the exposure.
  - Refuses if the account is not flat, so it can never be confused with, or
    interfere with, a real position.
  - Refuses if the rule engine says the account may not open.
  - Closes what it opened, even if verification fails — a smoke test that
    leaves a position behind is worse than no smoke test. See `finally`.
  - Refuses if the projected risk exceeds a hard ceiling, so a mis-scaled
    price cannot turn "tiny test" into a real loss. This is not theoretical:
    a 1000x trendbar scaling bug on 2026-08-05 produced a negative stop that
    the sizer costed as if it were $199 of risk.

Usage:
  python3 ftmo_smoke_order.py                # dry: show what it WOULD do
  python3 ftmo_smoke_order.py --confirm      # place, verify, close
"""

from __future__ import annotations

import argparse
import sys
import time

import ftmo_rules as fr
import ftmo_service as svc
import ftmo_session as fs
import ftmo_signal as sig

# The most granular instrument in the universe, so the test costs as little
# as the venue permits. min_volume 1 centi-unit = 0.01 index units.
DEFAULT_SYMBOL = "US30.cash"

# Hard ceiling on what this script may ever risk, in account currency. A
# scaling bug cannot spend more than this.
MAX_TEST_RISK_USD = 60.0


def run(symbol: str = DEFAULT_SYMBOL, confirm: bool = False,
        settle_s: float = 4.0) -> int:
    specs = svc.load_symbol_specs()
    spec = specs.get(symbol)
    if spec is None:
        print(f"{symbol} is not in the symbol capture", file=sys.stderr)
        return 2

    session = fs.FTMOSession(specs=specs)
    print(f"connecting to {svc.host_choice(session.env)} ...")
    session.start()
    print(f"connected, account {session.account_id}")
    opened = False

    try:
        positions = session.refresh_positions()
        if positions:
            print(f"REFUSING: account is not flat ({len(positions)} open). This "
                  f"test must never be confused with a real position.",
                  file=sys.stderr)
            return 3

        acct = session.account()
        state = fr.AccountState(equity=acct["balance"], balance=acct["balance"],
                                day_start_balance=acct["balance"])
        verdict = fr.evaluate(fr.FTMOConfig(), state)
        if not verdict.can_open:
            print(f"REFUSING: rule engine says no new entries — "
                  f"{'; '.join(verdict.reasons)}", file=sys.stderr)
            return 4
        print(f"rule engine: {verdict.summary()}")

        # Ask the venue's own calendar before spending a round trip on an
        # order it will refuse. UNKNOWN (None) is allowed through — a missing
        # schedule is missing information, and the venue stays the authority.
        is_open = fs.market_open_now(spec)
        if is_open is False:
            print(f"REFUSING: {symbol} is outside its trading schedule "
                  f"({spec.get('schedule_timezone')}). A streaming quote does "
                  f"not mean a tradeable market.", file=sys.stderr)
            return 9

        session.subscribe([symbol])
        deadline = time.time() + 15
        while time.time() < deadline and session.quote(symbol) is None:
            time.sleep(0.3)
        q = session.quote(symbol)
        if q is None or not q.ask:
            print(f"REFUSING: no quote for {symbol} — cannot price a stop",
                  file=sys.stderr)
            return 5

        # Cross-check bars against the live quote before trusting either. This
        # is the guard that the 1000x scaling bug would have tripped.
        session.assert_bars_match_quote(symbol)

        bars = session.trendbars(symbol, "D1", 60)
        atr = sig.atr_from_bars(bars)
        entry = q.ask
        stop = round(entry - 2 * atr, spec["digits"])
        volume = spec["min_volume"]
        units = volume / 100.0
        risk = units * (entry - stop)

        print(f"\n{symbol}: ask {entry:,.5f}  ATR {atr:,.5f}")
        print(f"  volume {volume} ({units:g} units)  stop {stop:,.5f}")
        print(f"  risk if stopped: ${risk:,.2f}")

        fs.validate_stop("BUY", entry, stop)
        if risk > MAX_TEST_RISK_USD:
            print(f"REFUSING: ${risk:,.2f} exceeds this script's ${MAX_TEST_RISK_USD:,.2f} "
                  f"ceiling. Suspect a bad price, not a big market.",
                  file=sys.stderr)
            return 6

        if not confirm:
            print("\nDRY RUN — nothing placed. Re-run with --confirm to place, "
                  "verify and close.")
            return 0

        print("\nplacing ...")
        res = session.place_market(symbol, "BUY", volume, stop, entry,
                                   label="smoke")
        opened = True
        print(f"  sent: {res}")

        time.sleep(settle_s)
        positions = session.refresh_positions()
        if not positions:
            print("\nNO POSITION APPEARED. The order was accepted but nothing "
                  "opened — do not trust the order path.", file=sys.stderr)
            return 7

        p = positions[0]
        print(f"\nread back from the venue:")
        print(f"  position {p.position_id}  {p.symbol}  {p.side}  vol={p.volume}")
        print(f"  entry {p.entry_price:,.5f}")
        print(f"  stopLoss {p.stop_loss if p.stop_loss else 'NONE'}")
        print(f"  protected: {p.protected}")

        ok = True
        if not p.protected:
            print("\nFAIL: the venue reports NO stop on the position. Every "
                  "other FTMO module assumes there is one.", file=sys.stderr)
            ok = False
        else:
            drift = abs(p.stop_loss - stop)
            tol = 10 ** -(spec["digits"] - 1) if spec["digits"] > 1 else 0.5
            if drift > max(tol, abs(stop) * 0.01):
                print(f"\nFAIL: stop came back at {p.stop_loss} but we asked "
                      f"for {stop} (drift {drift}).", file=sys.stderr)
                ok = False
            else:
                print(f"\nPASS: server-side stop attached at entry, within "
                      f"{drift:.5f} of the requested price.")
        return 0 if ok else 8

    finally:
        # Close whatever we opened, whatever happened above. A smoke test that
        # leaves a live position behind is worse than not running one.
        if opened:
            try:
                left = session.refresh_positions()
                for p in left:
                    print(f"closing position {p.position_id} ({p.symbol}) ...")
                    session.close_position(p.position_id, p.volume)
                time.sleep(settle_s)
                remaining = session.refresh_positions()
                if remaining:
                    print(f"WARNING: {len(remaining)} position(s) STILL OPEN "
                          f"after close — check the account by hand.",
                          file=sys.stderr)
                else:
                    print("account flat again.")
            except Exception as e:                            # noqa: BLE001
                print(f"WARNING: cleanup failed ({e}). CHECK THE ACCOUNT.",
                      file=sys.stderr)
        session.stop()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prove the FTMO order path with one minimum-size trade.")
    ap.add_argument("--symbol", default=DEFAULT_SYMBOL)
    ap.add_argument("--confirm", action="store_true",
                    help="Actually place the order. Without this it is a dry run.")
    args = ap.parse_args()
    return run(args.symbol, confirm=args.confirm)


if __name__ == "__main__":
    sys.exit(main())
