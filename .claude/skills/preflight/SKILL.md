---
name: preflight
description: Verify this machine is actually running what git says it is — pull state, the armed flag, launchd job health, cTrader token expiry, all twelve offline selftests, and the bot's last recorded decision. Read-only, no venue connection. Use before trusting a fix, before writing a handoff, after any merge that touches runner code, and when returning to the project after time away.
disable-model-invocation: true
---

# Preflight

Run the check, then read it out loud. The script is the evidence; your job is
to say what it means and what to do about it.

```bash
.venv/bin/python3 .claude/skills/preflight/preflight.py
```

It takes about 15 seconds, contacts no broker, prints no credential, and works
when the venue is unreachable — which is precisely when it matters.

## Why this exists

Handoff 6 §4: PR #13 was merged at 22:09, and the 22:30 firing still rejected
three orders. **launchd runs the runner from the main checkout's working tree,
and that tree was three commits behind.** GitHub said merged; the machine ran
the old code.

That is one instance of the question this project keeps failing to ask: *is the
thing I believe is running, running?* Every other item this checks is the same
question wearing different clothes — a monitor that had been watching nothing
for a week, an armed flag one stray `git checkout .` from flipping, 22 sleep
failures unnoticed for 19 hours, an access token with no auto-refresh.

## Reading the output

**§1 — behind origin is the serious one.** `BAD` here means the code you
merged is not the code that will fire at :30. Fix with `git pull` in the main
checkout, then re-run. Uncommitted paths are a warning, not a failure, except:

**§2 — an uncommitted armed flag that differs from HEAD is `BAD`.** Either
direction is a silent state change: reverting can disarm a bot you think is
running, or re-arm one you think you stopped. Commit it or drop it, deliberately.

**§3 — log growth is the authoritative signal.** `launchctl list` from a
sandboxed shell reports a different domain and will show loaded jobs as
missing. If the FTMO log has not been written in over 75 minutes, a firing was
missed — check `ftmo_launchd.log` for `SessionError` (the Mac sleeping wears
two different error messages; both are the same event).

**§4 — the token has no auto-refresh.** `python3 ftmo_service.py --refresh`.

**§5 — twelve modules, 579 checks.** If any fail, nothing else in the report
matters yet. `ftmo_audit` prints `AUDIT WRITE FAILED` to stderr on a *passing*
test; the script reads exit codes only, so a module named here has really failed.

**§6 — this is read from `ftmo_audit/*.jsonl`, not the venue.** It is what the
bot last *decided*, which is not the same as what the account currently *is*.
`BREACHED` means the rule engine has stopped opening new positions. Note
`must_flatten` is False whenever nothing is open — that is correct, not a miss.

**§7 — the 46 `venue=ibkr` rows must never drop below 46** (rule 6). Research
notes older than 7 days mean the cadence has slipped, which is the project's
actual bottleneck.

## What it deliberately does not do

It does not contact the venue, so it cannot tell you the live balance, the real
open positions, or whether a stop is actually attached at the broker. Nothing
here substitutes for reading the venue back — rule 2. When you need that:

```bash
.venv/bin/python3 ftmo_service.py --probe        # read-only account probe
.venv/bin/python3 ftmo_runner.py --reconcile     # detect + journal closes
.venv/bin/python3 ftmo_runner.py --force --dry-run   # full pipeline, places nothing
```

One caveat worth stating every time it comes up: **a firing that succeeded
while a Claude Code session was running proves little about the sleep problem**,
because the session itself holds `caffeinate` assertions. The real test is a
firing with no session running.
