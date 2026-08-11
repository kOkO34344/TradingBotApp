#!/usr/bin/env python3
"""preflight.py — is THIS MACHINE running what git says it is?

Read-only. No venue connection, no torch, no credentials printed. Everything
here is answerable from disk, which is deliberate: the checks must still work
when the broker is unreachable, because that is exactly when you need them.

WHY THIS EXISTS
---------------
Handoff 6, section 4: PR #13 was merged at 22:09 and the 22:30 firing still
rejected three orders, because launchd runs the runner from the MAIN CHECKOUT'S
WORKING TREE and that tree was three commits behind. GitHub said merged; the
machine was running the old code.

That is one instance of a general question this project keeps failing to ask:
*is the thing I believe is running, running?* The others on the record — a
monitor that had been watching nothing for a week, an armed flag one stray
`git checkout .` from flipping, 22 sleep failures nobody saw for 19 hours, a
token with no auto-refresh — are all the same question.

Usage:  .venv/bin/python3 .claude/skills/preflight/preflight.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

OK, WARN, BAD, INFO = "  ok  ", " WARN ", " BAD  ", " ..   "


def line(status: str, label: str, detail: str = "") -> None:
    print(f"[{status}] {label}" + (f"  —  {detail}" if detail else ""))


def sh(*args: str, cwd: Path | None = None, timeout: int = 30) -> str | None:
    try:
        proc = subprocess.run(
            args, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.1f}d ago"


# ----------------------------------------------------------------- 1. the code
def check_git() -> None:
    print("\n== 1. Is the code on disk the code you think it is ==")
    branch = (sh("git", "rev-parse", "--abbrev-ref", "HEAD") or "?").strip()
    head = (sh("git", "log", "--oneline", "-1") or "?").strip()
    line(INFO, f"branch {branch}", head)

    sh("git", "fetch", "--quiet", timeout=25)
    counts = sh("git", "rev-list", "--left-right", "--count", f"{branch}...origin/{branch}")
    if counts:
        ahead, behind = (counts.split() + ["0", "0"])[:2]
        if int(behind):
            line(
                BAD, f"{behind} commit(s) BEHIND origin",
                "launchd runs THIS working tree. Merged is not running — `git pull`.",
            )
        elif int(ahead):
            line(WARN, f"{ahead} commit(s) ahead of origin (unpushed)")
        else:
            line(OK, "in sync with origin")
    else:
        line(WARN, "could not compare with origin", "offline, or no upstream")

    dirty = (sh("git", "status", "--porcelain") or "").strip()
    if dirty:
        # porcelain v1 is "XY <path>", but the status field can be one or two
        # characters wide depending on the state — slicing a fixed offset eats
        # the first letter of the filename. Split on whitespace instead.
        files = [ln.split(maxsplit=1)[-1] for ln in dirty.splitlines() if ln.strip()]
        line(WARN, f"{len(files)} uncommitted path(s)", ", ".join(files[:6]))
    else:
        line(OK, "working tree clean")


# ---------------------------------------------------------------- 2. the flag
def check_armed() -> None:
    print("\n== 2. Is the runner armed, and is that state safe from a revert ==")
    settings = ROOT / "trader_settings.json"

    def armed(blob: str | None) -> bool | None:
        if blob is None:
            return None
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, ValueError):
            return None
        return bool(((data.get("ftmo") or {}).get("autotrade") or {}).get("enabled", False))

    try:
        working = armed(settings.read_text(encoding="utf-8"))
    except OSError:
        working = None
    committed = armed(sh("git", "show", "HEAD:trader_settings.json"))

    if working is None:
        line(BAD, "cannot read trader_settings.json")
        return
    line(OK if working else INFO, f"ftmo.autotrade.enabled = {working}",
         "the runner WILL place orders in-window" if working else "disarmed — it will no-op")

    status = (sh("git", "status", "--porcelain", "--", "trader_settings.json") or "").strip()
    if status and committed is not None and committed != working:
        line(
            BAD, "the armed flag is UNCOMMITTED and differs from HEAD",
            f"working={working} HEAD={committed} — one `git checkout .` flips it silently",
        )
    elif status:
        line(WARN, "trader_settings.json is modified", "flag itself unchanged vs HEAD")
    else:
        line(OK, "flag is committed", "a revert cannot silently change it")


# ------------------------------------------------------------- 3. the schedule
# Each job maps to the log it writes on a NORMAL run. Deliberately NOT the
# *_launchd.log stderr sinks: those are empty until something goes wrong, so
# reading their mtime reports a healthy job as 20 days stale.
JOBS = {
    "com.tradingbotapp.ftmo": "ftmo_launchd.log",
    "com.tradingbotapp.vaultsync": "vault_sync.log",
    "com.tradingbotapp.dailydigest": "daily_digest.log",
    "com.tradingbotapp.dailydigestevening": "daily_digest.log",
}


def check_launchd() -> None:
    print("\n== 3. Are the scheduled jobs alive ==")
    print("     (log growth is the authoritative signal — `launchctl list` from a")
    print("      sandboxed shell reports a different domain and lies)")
    now = time.time()
    for label, logname in JOBS.items():
        plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        log = ROOT / logname
        if not plist.exists():
            line(WARN, label, "no plist installed")
            continue
        if not log.exists():
            line(WARN, label, f"{logname} does not exist — never run?")
            continue
        since = now - log.stat().st_mtime
        if label.endswith("ftmo"):
            # hourly at :30, so anything past ~75 minutes is a missed firing
            status = OK if since < 4500 else BAD
            note = "" if since < 4500 else "MISSED FIRING(S) — check for sleep failures"
        else:
            status = OK if since < 172800 else WARN
            note = "" if since < 172800 else "stale"
        line(status, f"{label:38s} {logname}", f"last wrote {age(since)} {note}".strip())


# ---------------------------------------------------------------- 4. the token
def check_token() -> None:
    print("\n== 4. Credentials ==")
    env = ROOT / "secrets" / "ctrader.env"
    if not env.exists():
        line(WARN, "secrets/ctrader.env not found", "run: python3 secrets_store.py --describe")
        return
    expires_at = None
    try:
        for raw in env.read_text(encoding="utf-8").splitlines():
            if raw.startswith("CTRADER_TOKEN_EXPIRES_AT="):
                expires_at = raw.split("=", 1)[1].strip()
                break
    except OSError:
        pass
    if not expires_at or not expires_at.isdigit():
        line(WARN, "cTrader token expiry unknown", "no CTRADER_TOKEN_EXPIRES_AT recorded")
        return
    left = int(expires_at) - time.time()
    when = datetime.fromtimestamp(int(expires_at), timezone.utc).strftime("%Y-%m-%d")
    if left <= 0:
        line(BAD, f"cTrader access token EXPIRED {when}", "run: python3 ftmo_service.py --refresh")
    elif left < 7 * 86400:
        line(BAD, f"cTrader token expires {when}", f"{left / 86400:.1f} days left — refresh NOW")
    elif left < 21 * 86400:
        line(WARN, f"cTrader token expires {when}", f"{left / 86400:.0f} days left, no auto-refresh")
    else:
        line(OK, f"cTrader token valid until {when}", f"{left / 86400:.0f} days")


# ------------------------------------------------------------- 5. the selftests
def check_selftests() -> None:
    print("\n== 5. Offline selftests ==")
    modules = [
        p for p in sorted(ROOT.glob("*.py"))
        if "--selftest" in p.read_text(encoding="utf-8", errors="ignore")
    ]
    py = ROOT / ".venv" / "bin" / "python3"
    if not py.is_file():
        line(BAD, "no .venv/bin/python3", "conda base has no torch — see CLAUDE.md")
        return
    failed = []
    for module in modules:
        try:
            proc = subprocess.run(
                [str(py), module.name, "--selftest"],
                cwd=ROOT, capture_output=True, timeout=60,
            )
            if proc.returncode != 0:
                failed.append(module.name)
        except (OSError, subprocess.TimeoutExpired):
            failed.append(f"{module.name} (did not complete)")
    if failed:
        line(BAD, f"{len(failed)}/{len(modules)} FAILED", ", ".join(failed))
    else:
        line(OK, f"{len(modules)}/{len(modules)} modules pass",
             "note: ftmo_audit prints 'AUDIT WRITE FAILED' and still passes — exit codes only")


# --------------------------------------------------------------- 6. the account
def check_posture() -> None:
    print("\n== 6. What the bot last decided (from ftmo_audit/, no venue needed) ==")
    files = sorted((ROOT / "ftmo_audit").glob("*.jsonl"))
    if not files:
        line(WARN, "no audit files")
        return
    latest = None
    for raw in reversed(files[-1].read_text(encoding="utf-8").splitlines()):
        try:
            event = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if event.get("kind") == "RUNNER_EVALUATION":
            latest = event
            break
    if latest is None:
        line(WARN, f"no RUNNER_EVALUATION in {files[-1].name}")
        return

    breached = latest.get("breached")
    equity = latest.get("equity")
    line(
        BAD if breached else OK,
        "BREACHED" if breached else "posture ok",
        f"equity {equity:,.2f}" if isinstance(equity, (int, float)) else "",
    )
    for used, soft, hard, name in (
        ("daily_loss_used", "daily_soft", "daily_hard", "daily loss"),
        ("drawdown_used", "drawdown_soft", "drawdown_hard", "drawdown "),
    ):
        u, s, h = latest.get(used), latest.get(soft), latest.get(hard)
        if all(isinstance(v, (int, float)) for v in (u, s, h)):
            status = BAD if u >= h else (WARN if u >= s else OK)
            line(status, f"{name} {u:>10,.2f}", f"soft {s:,.2f}  hard {h:,.2f}")
    line(INFO, "can_open", str(latest.get("can_open")))
    line(INFO, "must_flatten", str(latest.get("must_flatten")))


# ---------------------------------------------------------------- 7. the record
def check_journal() -> None:
    print("\n== 7. The audit trail and the research cadence ==")
    journal = ROOT / "trade_journal.csv"
    if journal.exists():
        rows = journal.read_text(encoding="utf-8", errors="ignore").splitlines()
        body = [r for r in rows[1:] if r.strip()]
        last = body[-1].split(",")[0] if body else "?"
        venues: dict[str, int] = {}
        for row in body:
            venue = row.rsplit(",", 1)[-1].strip()
            venues[venue] = venues.get(venue, 0) + 1
        line(INFO, f"{len(body)} journal rows", ", ".join(f"{k}={v}" for k, v in sorted(venues.items())))
        try:
            when = datetime.fromisoformat(last)
            line(OK, "last row", f"{last}  ({age(time.time() - when.timestamp())})")
        except ValueError:
            line(INFO, "last row", last)
        if venues.get("ibkr", 0) < 46:
            line(BAD, "fewer than 46 venue=ibkr rows", "rule 6: those rows stay forever")
    notes = sorted((ROOT / "research_log").glob("*.md")) if (ROOT / "research_log").exists() else []
    if notes:
        newest = max(n.stat().st_mtime for n in notes)
        days = (time.time() - newest) / 86400
        line(
            OK if days < 7 else WARN,
            f"{len(notes)} research notes",
            f"newest {age(time.time() - newest)}" + ("  — cadence is 7d, OVERDUE" if days >= 7 else ""),
        )


def main() -> int:
    print("=" * 72)
    print("TradingBotApp preflight — read-only, no venue connection")
    print(f"root: {ROOT}")
    print("=" * 72)
    for check in (check_git, check_armed, check_launchd, check_token,
                  check_selftests, check_posture, check_journal):
        try:
            check()
        except Exception as exc:  # a broken check must not hide the others
            line(WARN, f"{check.__name__} did not complete", repr(exc))
    print("\n" + "=" * 72)
    print("Nothing above contacted the venue. To verify the account itself:")
    print("  .venv/bin/python3 ftmo_service.py --probe        (read-only)")
    print("  .venv/bin/python3 ftmo_runner.py --reconcile     (records closes)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
