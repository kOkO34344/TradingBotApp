# IBKR / TWS Connection — Diagnostic Report

**Date:** 2026-07-21
**Reference:** [IBKR TWS API Documentation](https://www.interactivebrokers.eu/campus/ibkr-api-page/twsapi-doc/)

## TL;DR

The code side (`ibkr_service.py`, `ib_async`, the Python env) is completely fine
and was never the problem. **IB Gateway is installed and running, but sitting at
the login screen, not yet logged into the paper account** — so its API socket
has never opened. This matches `CLAUDE.md`'s existing note: *"TWS not yet
installed on the owner's Mac — first connected smoke test still pending."*
Half of that's now out of date (it *is* installed) — the smoke test is the
one remaining step, and it's blocked purely on login, which only you can do.

## What we found (evidence, not guesses)

Ran `diagnose_ibkr.sh` plus direct process/port/log inspection:

| Check | Result |
|---|---|
| TWS/Gateway process running | ✅ Yes — `IB Gateway 10.45` (PID 7715), started 14:01 |
| Install location | `~/Applications/IB Gateway 10.45/` (diagnose_ibkr.sh originally missed this because it looked for an exact folder name without the version suffix — fixed, see below) |
| API port open (7497/4002/7496/4001) | ❌ None open |
| Open network sockets on the Gateway process (`lsof`) | **None at all** — it hasn't even reached out to IBKR's servers for auth yet |
| `~/Jts/launcher.log` | Confirms the app fully booted and is currently rendering the login screen (fetching the login-page news bulletin panel), no login attempt logged yet |
| `~/Jts/jts.ini` | `tradingMode=l` — the login screen was last left on **Live**, not Paper. **You'll need to switch it to Paper when you log in.** |
| Python venv | `.venv` exists, Python 3.13 |
| `ib_async` | Installed, version 2.1.0 — importable, no issues |
| `ibkr_service.py --selftest` (offline, 18 checks) | ✅ ALL PASS |
| `trader_settings.json` → `ibkr_port` | `7497` (TWS paper) — fine as-is; Gateway paper is 4002, the connected smoke test now checks both automatically |

**Conclusion: this was never a code bug.** Every layer of `ibkr_service.py` —
contract builders, RiskGuard, journal, bracket validation — passes offline.
The only missing link is that nothing has logged into the Gateway yet.

## What I did from the terminal

1. Ran the existing `diagnose_ibkr.sh` and cross-checked it with `ps`, `lsof`,
   and the Gateway's own `launcher.log`/`jts.ini` to pin down the exact state
   (login screen, not-yet-authenticated) rather than guessing from a closed port alone.
2. **Fixed a bug in `diagnose_ibkr.sh`**: it looked for an install folder named
   exactly `IB Gateway`, but your actual install is `IB Gateway 10.45` (version
   suffix), so it always reported "not found" even though it was. Now uses a
   wildcard match.
3. **Added `wait_and_test_ibkr.sh`** — a new watcher script. It polls the two
   *paper* ports only (7497 TWS / 4002 Gateway — deliberately never checks the
   live ports 7496/4001, matching the paper-first rule already in
   `ibkr_service.py`/`CLAUDE.md`). The moment a paper port opens, it
   automatically runs the connected smoke test (`connect()` →
   `verify_paper_account()` → pull 15-min AAPL bars → disconnect) and logs
   everything to `ibkr_connection_test.log`.
4. **Started it running in the background right now** (15-minute window). You
   don't need to do anything else in the terminal — just finish the login
   steps below in the Gateway window, and the smoke test will fire itself.
   Check `ibkr_connection_test.log` in this folder any time to see the result,
   or ask me and I'll check the process for you.

## What only you can do (credentials, GUI, 2FA — not things I can or should touch)

1. **Switch to Paper Trading on the login screen.** The Gateway remembers
   "Live" from last time (`tradingMode=l` in `jts.ini`). On the login window
   there's a toggle/mode selector — set it to **Paper Trading** before entering
   credentials, otherwise you risk authenticating against your live account.
2. **Log in** with your paper trading username/password and complete
   **2FA** (IB Key / Handy Key / SMS / Digital Security Card+ — Security Code
   Cards and Temporary Code Cards are not supported by the API side at all).
3. **Enable the API.** Once logged in: `Configure → Settings → API → Settings`
   (Gateway) — or `Edit → Global Configuration → API → Settings` in TWS:
   - Check **"Enable ActiveX and Socket Clients"**
   - Uncheck **"Read-Only API"** (needed later for order placement; harmless now)
   - Confirm **Socket port = 7497** (TWS) matches `trader_settings.json`, or
     **4002** if you're intentionally using Gateway's default — either is fine,
     the watcher checks both.
   - Under the same screen, confirm **Trusted IPs** includes `127.0.0.1` (it
     already does, per `jts.ini`) so you won't get a connection popup, or just
     click **Accept** if a "Confirm incoming connection" dialog appears.
4. **Recommended, not required:** `Configure → Lock and Exit` →
   enable "Never lock" / auto-restart, so Gateway doesn't lock mid-session
   during longer work sessions. Also worth bumping memory allocation to
   ~4000MB under `Configure → Settings → General` if you'll be running this
   for extended periods (IBKR's own recommendation for API users).

Once you've done steps 1–3, the background watcher does the rest — no need
to run anything by hand.

## Files touched this session

- `diagnose_ibkr.sh` — bug fix (install-folder detection)
- `wait_and_test_ibkr.sh` — new: paper-port watcher + auto smoke test
- `ibkr_connection_test.log` — created by the watcher once it finds an open port
- `ibkr_setup_report.md` — this file

## Next step after this is green

Per `CLAUDE.md`'s work queue, once the connected smoke test passes: commit,
then move to building `paper_trader.py` (Phase 3 — momentum-rotation signal →
proposed order → your approval → `place_bracket_order` on paper → journal).
Nothing here changes that plan; this was purely unblocking the login step.

---

## Session 2 — 2026-07-21, later same day

### What changed since Session 1

- The GUI mode toggle **did get switched to Paper** — confirmed in `~/Jts/jts.ini`:
  `tradingMode=p` (was `l` in Session 1). Trusted IPs, ApiOnly, and
  RemotePortOrderRouting settings all look normal.
- **Still no login has completed.** 60+ minutes of `launcher.log` after the
  mode switch show only the login screen's background bulletin polling
  (`GstatMessageMgr`, `"ut":"paper"`, every ~3 min) — no `AuthDispatcher`
  activity, no credential submission, no error response. The app is sitting
  idle at the login screen, not failing a login attempt.
- The background watcher (`wait_and_test_ibkr.sh`) ran its full 900s window
  and timed out — expected, since the port never opened (see below).

### Root cause (confirmed by the owner, not inferred from logs)

**This was never a code, config, or TWS-settings problem.** The account has
an **address-verification issue on IBKR's side, currently in manual review.**
Until IBKR's compliance team clears that review, there's nothing to log into —
Gateway can't open an API session against an account that isn't fully
provisioned yet, paper or live.

This also explains Session 1's evidence in hindsight: nothing was ever
submitted to the login form not because of a UI mystery, but because logging
in would have failed or been pointless until the account clears review. We
were troubleshooting a symptom of an account-status issue, not a technical
outage of TWS/Gateway/the API.

### What I did this session

1. Re-ran the same diagnostic checks as Session 1 (`ps`, `lsof`,
   `~/Jts/launcher.log`, `~/Jts/jts.ini`) to confirm nothing had silently
   changed and to verify the mode-switch had taken effect.
2. Attempted to use the Chrome browser tool to navigate IBKR's site live —
   **the local Chrome extension bridge didn't respond** (timed out after
   several minutes). If live browser navigation is wanted in a future
   session, check that the Claude for Chrome extension is running and
   connected before asking for it.
3. Fell back to web search + fetch of IBKR's own documentation to confirm
   the mechanics of paper account provisioning (see references below), so
   the "what to do once cleared" steps here are accurate and sourced from
   IBKR directly, not guessed.

### How IBKR paper accounts actually get provisioned (for reference)

Per IBKR's own docs ([Paper Trading Account](https://www.ibkrguides.com/clientportal/papertradingaccount.htm),
[About Paper Trading Accounts](https://www.ibkrguides.com/clientportal/aboutpapertradingaccounts.htm)):

- New account holders normally get a paper trading account automatically;
  older accounts may not and have to request one.
- Request path (once the live account is fully approved): Client Portal →
  user menu (top-right head-and-shoulders icon) → **Settings → Account
  Configuration → Paper Trading Account** → set unique paper-account
  credentials (separate from the live account's login).
- Provisioning normally takes **under 24 hours**, with an email notification
  when it's ready.
- The paper account mirrors the live account's trading permissions, market
  data subscriptions, and base currency — another reason it can't be
  provisioned before the live account itself is fully approved.

None of that can happen before the address-verification review clears, since
the paper account is provisioned against an approved live account.

### While waiting on IBKR

Nothing to do in this codebase. Practically, worth checking on the IBKR side:

- The email associated with the application, in case they've requested
  specific documents (commonly accepted proof-of-address: a recent utility
  bill, bank statement, or government-issued letter showing the address —
  note mobile phone bills are explicitly not accepted as proof of address).
- Client Portal's Message Center periodically, in case they need something
  from you rather than it just being processing time.
- No need to keep IB Gateway open and idle while waiting — the watcher
  script re-runs in seconds whenever it's actually useful again.

### Plan for when IBKR clears the review

1. Log into Client Portal, go to **Settings → Account Configuration → Paper
   Trading Account**, set it up if it isn't already there, wait for the
   confirmation email (≤24h).
2. Open IB Gateway 10.45, select **Paper** mode on the login screen (already
   set from this session — worth double-checking `tradingMode=p` is still
   set in `~/Jts/jts.ini` if it's been a while), log in with the **paper
   account's own credentials** (not the live account's).
3. In Gateway: `Configure → Settings → API → Settings` → enable "ActiveX and
   Socket Clients", disable "Read-Only API", confirm port 7497 or 4002.
4. Run `./wait_and_test_ibkr.sh` (or just have it already running in the
   background before logging in — it polls, so order doesn't matter). It
   auto-detects the open port and runs the connected smoke test.
5. Once that passes: commit, then resume `CLAUDE.md`'s work queue item 2 —
   `paper_trader.py`, the Phase 3 approval loop.

### Files in this folder relevant to this issue

- `ibkr_setup_report.md` — this file (Session 1 + Session 2)
- `diagnose_ibkr.sh` — one-shot health check, safe to re-run any time
- `wait_and_test_ibkr.sh` — paper-port watcher + auto smoke test
- `ibkr_connection_test.log` — output from the watcher runs so far (both
  timed out, as expected given the account status)
