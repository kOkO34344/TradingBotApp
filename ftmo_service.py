#!/usr/bin/env python3
"""
ftmo_service.py — FTMO venue adapter over the cTrader Open API.

The broker layer for the FTMO side, the counterpart to `ibkr_service.py`.
Same division of responsibility: this module talks to the venue, and
`ftmo_rules.py` decides whether a trade is allowed. Neither imports the other's
job, so the rule engine stays pure and testable offline and this file stays a
transport.

WHY cTrader AND NOT MT5. The official MetaTrader5 Python package is Windows
only and has no distribution for this Apple Silicon Mac, so the usual prop-firm
path (Python driving an MT5 terminal) would need a VM or a rented VPS. cTrader
Open API is protobuf over TLS and runs natively here. FTMO supports it, and
issues a cTrader ID in the Client Area.

TWISTED, IN AN ASYNCIO PROJECT. The cTrader SDK is built on Twisted while
`ib_async` and the FastAPI backend are asyncio. They can share a process, but
only via Twisted's asyncio reactor, and it must be installed BEFORE anything
imports the default reactor. `install_asyncio_reactor()` below does that and is
a no-op once a reactor exists. The CLI paths in this file run standalone and
use the default reactor, which is why they are safe today — but anything that
later imports this module into the web backend must call that installer first.

CREDENTIALS live in a gitignored `.env` (see `.env` for the key names). They
are never logged, never printed, and never interpolated into a shell command.

Usage:
  python3 ftmo_service.py --authorize     # one-time OAuth, writes tokens to .env
  python3 ftmo_service.py --probe         # connect, list accounts, show state
  python3 ftmo_service.py --selftest      # offline checks, no network
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"

# cTrader quotes prices and money as scaled integers. Money is in "cents of a
# cent": divide by 100 to get the account currency. Getting this wrong by a
# factor of 100 would misreport equity and therefore every FTMO limit, so it is
# named rather than sprinkled as a literal.
MONEY_SCALE = 100.0


# ------------------------------------------------------------------- env I/O

def load_env(path: Path = ENV_FILE) -> dict:
    """Parse a simple KEY=VALUE .env. Blank values are returned as "".

    Deliberately not python-dotenv: this needs no dependency, and a file this
    small is clearer parsed in place than behind a library.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Create it with your cTrader credentials — see "
            f"the key names in the repo's .env template.")
    out = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def save_env_value(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Set one key in .env, preserving comments, order and every other value.

    Rewrites in place rather than appending, so re-authorising doesn't leave
    two conflicting CTRADER_ACCESS_TOKEN lines where the last one silently
    wins.
    """
    lines = path.read_text().splitlines()
    found = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)


def require(env: dict, *keys: str) -> list:
    """Fetch required keys, failing with a message that names what is missing
    rather than a KeyError or, worse, an empty string sent to the broker."""
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing from .env: {', '.join(missing)}. "
            f"Run `python3 ftmo_service.py --authorize` if these are tokens.")
    return [env[k] for k in keys]


def host_for(env: dict) -> str:
    from ctrader_open_api import EndPoints
    choice = (env.get("CTRADER_HOST") or "demo").strip().lower()
    if choice not in ("demo", "live"):
        raise ValueError(f"CTRADER_HOST must be 'demo' or 'live', got {choice!r}")
    return (EndPoints.PROTOBUF_DEMO_HOST if choice == "demo"
            else EndPoints.PROTOBUF_LIVE_HOST)


def install_asyncio_reactor() -> bool:
    """Make Twisted run on the asyncio event loop. No-op if already installed.

    Only matters when this module is imported alongside ib_async or FastAPI.
    Must run before any other import pulls in twisted.internet.reactor, which
    is why it is a function called early rather than an import side effect.
    """
    try:
        from twisted.internet import asyncioreactor
        asyncioreactor.install()
        return True
    except Exception:
        return False  # a reactor is already installed; nothing to do


# --------------------------------------------------------------------- OAuth

class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the ?code=... redirect from cTrader's consent page."""
    code: str | None = None
    error: str | None = None

    def do_GET(self):  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            body = b"<h2>Authorised.</h2><p>You can close this tab.</p>"
        else:
            _CallbackHandler.error = params.get("error", ["no code in redirect"])[0]
            body = b"<h2>Authorisation failed.</h2><p>Check the terminal.</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass  # the default handler logs the full URL, which contains the code


def authorize(env: dict | None = None, timeout_s: int = 300) -> dict:
    """One-time OAuth: browser consent, catch the code, exchange for tokens.

    Writes access and refresh tokens back to .env. The access token expires
    (cTrader currently issues ~30 days), which is why the refresh token is
    saved too — see refresh_tokens().
    """
    from ctrader_open_api import Auth

    env = env or load_env()
    client_id, client_secret, redirect = require(
        env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_REDIRECT_URI")

    parsed = urllib.parse.urlparse(redirect)
    port = parsed.port or 80
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        raise RuntimeError(
            f"CTRADER_REDIRECT_URI must point at localhost for this flow, got "
            f"{redirect!r}. It must ALSO match the URI registered on the app.")

    auth = Auth(client_id, client_secret, redirect)
    url = auth.getAuthUri(scope="trading")

    _CallbackHandler.code = None
    _CallbackHandler.error = None
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1
    thread = threading.Thread(target=_serve_until_code, args=(server, timeout_s),
                              daemon=True)
    thread.start()

    print(f"Opening your browser to authorise this app on cTrader.")
    print(f"If it doesn't open, paste this into a browser:\n\n  {url}\n")
    print(f"Log in with your FTMO cTrader ID "
          f"({env.get('CTRADER_ID', '<not set>')}) and approve access.")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    thread.join(timeout=timeout_s + 5)
    server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"Authorisation failed: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise RuntimeError(
            f"No authorisation code received within {timeout_s}s. The most "
            f"common cause is the redirect URI on the cTrader app not exactly "
            f"matching {redirect!r}.")

    token = auth.getToken(_CallbackHandler.code)
    if "accessToken" not in token:
        raise RuntimeError(f"Token exchange failed: {token.get('errorCode', token)}")

    save_env_value("CTRADER_ACCESS_TOKEN", token["accessToken"])
    save_env_value("CTRADER_REFRESH_TOKEN", token.get("refreshToken", ""))
    if token.get("expiresIn"):
        save_env_value("CTRADER_TOKEN_EXPIRES_AT",
                       str(int(time.time()) + int(token["expiresIn"])))
    print("Tokens written to .env (mode 600, gitignored). Not printed here.")
    return token


def _serve_until_code(server: HTTPServer, timeout_s: int) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _CallbackHandler.code or _CallbackHandler.error:
            return
        server.handle_request()


def refresh_tokens(env: dict | None = None) -> dict:
    """Exchange the refresh token for a fresh access token."""
    from ctrader_open_api import Auth
    env = env or load_env()
    client_id, client_secret, redirect, refresh = require(
        env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET",
        "CTRADER_REDIRECT_URI", "CTRADER_REFRESH_TOKEN")
    token = Auth(client_id, client_secret, redirect).refreshToken(refresh)
    if "accessToken" not in token:
        raise RuntimeError(f"Refresh failed: {token.get('errorCode', token)}")
    save_env_value("CTRADER_ACCESS_TOKEN", token["accessToken"])
    if token.get("refreshToken"):
        save_env_value("CTRADER_REFRESH_TOKEN", token["refreshToken"])
    if token.get("expiresIn"):
        save_env_value("CTRADER_TOKEN_EXPIRES_AT",
                       str(int(time.time()) + int(token["expiresIn"])))
    return token


def token_expiry_note(env: dict) -> str:
    """Human-readable token freshness, for the probe output."""
    raw = env.get("CTRADER_TOKEN_EXPIRES_AT") or ""
    if not raw.isdigit():
        return "expiry unknown"
    left = int(raw) - int(time.time())
    if left <= 0:
        return "EXPIRED — run --authorize or refresh"
    return f"expires in {left // 86400}d {(left % 86400) // 3600}h"


# ---------------------------------------------------------------- connection

class FTMOError(RuntimeError):
    """A ProtoOAErrorRes came back, or the venue refused something."""


def _raise_if_error(payload) -> None:
    name = type(payload).__name__
    if name in ("ProtoOAErrorRes", "ProtoErrorRes"):
        raise FTMOError(f"{getattr(payload, 'errorCode', '?')}: "
                        f"{getattr(payload, 'description', '')}")


def probe(env: dict | None = None, timeout_s: int = 45) -> int:
    """Connect, authenticate, list accounts and report account state.

    This is the "does the whole path actually work" check — it places nothing
    and changes nothing. Everything it prints comes from the venue.
    """
    from twisted.internet import reactor, defer
    from ctrader_open_api import Client, Protobuf, TcpProtocol

    env = env or load_env()
    client_id, client_secret, access_token = require(
        env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN")
    host = host_for(env)
    configured_account = (env.get("CTRADER_ACCOUNT_ID") or "").strip()

    print(f"cTrader Open API -> {host}:5035  (token {token_expiry_note(env)})")
    state = {"rc": 1}

    @defer.inlineCallbacks
    def run(client):
        try:
            res = yield client.send("ProtoOAApplicationAuthReq",
                                    clientId=client_id, clientSecret=client_secret)
            _raise_if_error(Protobuf.extract(res))
            print("application authenticated")

            res = yield client.send("ProtoOAGetAccountListByAccessTokenReq",
                                    accessToken=access_token)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            accounts = list(payload.ctidTraderAccount)
            if not accounts:
                print("\nNo trading accounts are visible for this access token.")
                print("Most likely causes, in order:")
                print("  1. The FTMO account was created AFTER the app was "
                      "authorised — re-run --authorize to re-consent.")
                print(f"  2. Wrong endpoint: CTRADER_HOST is '{env.get('CTRADER_HOST')}'"
                      f" — try the other one.")
                print("  3. The app's 'trading' scope was not granted.")
                state["rc"] = 2
                return

            print(f"\n{len(accounts)} trading account(s) visible:")
            for a in accounts:
                live = "LIVE" if getattr(a, "isLive", False) else "demo"
                print(f"  ctidTraderAccountId={a.ctidTraderAccountId}  {live}  "
                      f"broker={getattr(a, 'brokerName', '?')}")

            target = int(configured_account) if configured_account.isdigit() \
                else accounts[0].ctidTraderAccountId
            if not configured_account:
                print(f"\nCTRADER_ACCOUNT_ID is unset — probing the first account "
                      f"({target}).")

            res = yield client.send("ProtoOAAccountAuthReq",
                                    ctidTraderAccountId=target,
                                    accessToken=access_token)
            _raise_if_error(Protobuf.extract(res))
            print(f"account {target} authenticated")

            res = yield client.send("ProtoOATraderReq", ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            t = payload.trader
            digits = getattr(t, "moneyDigits", 2) or 2
            scale = float(10 ** digits)
            balance = t.balance / scale
            print(f"\naccount state:")
            print(f"  balance           {balance:,.2f}")
            print(f"  currency id       {getattr(t, 'depositAssetId', '?')}")
            print(f"  leverage          {getattr(t, 'leverageInCents', 0) / 100:.0f}x")
            print(f"  registered        {getattr(t, 'registrationTimestamp', 0)}")

            res = yield client.send("ProtoOAReconcileReq", ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            positions = list(payload.position)
            orders = list(payload.order)
            print(f"  open positions    {len(positions)}")
            print(f"  pending orders    {len(orders)}")
            for p in positions:
                td = p.tradeData
                sl = getattr(p, "stopLoss", 0)
                print(f"    symbolId={td.symbolId} {td.tradeSide} "
                      f"vol={td.volume} stopLoss={sl or 'NONE'}")

            res = yield client.send("ProtoOASymbolsListReq",
                                    ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            symbols = list(payload.symbol)
            print(f"\n{len(symbols)} tradeable symbols. First 15:")
            for s in symbols[:15]:
                print(f"    {s.symbolId:>8}  {s.symbolName}")

            state["rc"] = 0
        except Exception as e:
            print(f"\nPROBE FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            state["rc"] = 1
        finally:
            if reactor.running:
                reactor.stop()

    client = Client(host, 5035, TcpProtocol)
    client.setConnectedCallback(lambda c: run(c))
    client.setDisconnectedCallback(
        lambda c, reason: print(f"disconnected: {reason.getErrorMessage()}"))
    client.setMessageReceivedCallback(lambda c, m: None)
    client.startService()

    reactor.callLater(timeout_s, lambda: reactor.running and reactor.stop())
    reactor.run()
    return state["rc"]


# ------------------------------------------------------------------ selftest

def selftest() -> int:
    """Offline checks. No network, no credentials, no FTMO account touched."""
    import tempfile
    failures = []

    def check(name, cond):
        print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    print("env parsing:")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".env"
        p.write_text(
            "# a comment\n"
            "CTRADER_CLIENT_ID=abc\n"
            "\n"
            "CTRADER_CLIENT_SECRET=s3cret\n"
            "CTRADER_ACCESS_TOKEN=\n"
            "NOT_A_PAIR\n"
            "CTRADER_HOST=demo\n")
        env = load_env(p)
        check("comments and blank lines skipped", "# a comment" not in env)
        check("lines without '=' skipped", "NOT_A_PAIR" not in env)
        check("values parsed", env["CTRADER_CLIENT_ID"] == "abc")
        check("blank value is empty string, not missing",
              env.get("CTRADER_ACCESS_TOKEN") == "")

        print("require() names what is missing:")
        try:
            require(env, "CTRADER_CLIENT_ID", "CTRADER_ACCESS_TOKEN")
            check("blank token must be treated as missing", False)
        except RuntimeError as e:
            check("blank token treated as missing", "CTRADER_ACCESS_TOKEN" in str(e))
            check("...and the present key is not named", "CTRADER_CLIENT_ID" not in str(e))

        print("save_env_value rewrites in place:")
        save_env_value("CTRADER_ACCESS_TOKEN", "tok123", p)
        env2 = load_env(p)
        check("value updated", env2["CTRADER_ACCESS_TOKEN"] == "tok123")
        check("other values untouched", env2["CTRADER_CLIENT_SECRET"] == "s3cret")
        check("comment preserved", "# a comment" in p.read_text())
        occurrences = sum(1 for line in p.read_text().splitlines()
                          if line.startswith("CTRADER_ACCESS_TOKEN="))
        check("no duplicate key appended", occurrences == 1)
        save_env_value("BRAND_NEW_KEY", "v", p)
        check("a genuinely new key is appended", load_env(p)["BRAND_NEW_KEY"] == "v")
        check("file left mode 600", (p.stat().st_mode & 0o777) == 0o600)

        print("host selection:")
        check("demo maps to the demo endpoint",
              host_for({"CTRADER_HOST": "demo"}).startswith("demo."))
        check("live maps to the live endpoint",
              host_for({"CTRADER_HOST": "live"}).startswith("live."))
        check("unset defaults to demo, not live", host_for({}).startswith("demo."))
        check("case is ignored", host_for({"CTRADER_HOST": "DEMO"}).startswith("demo."))
        check("a typo is refused rather than defaulting to live",
              _raises(lambda: host_for({"CTRADER_HOST": "prod"})))

        print("missing .env fails clearly:")
        check("absent file names itself",
              _raises_with(lambda: load_env(Path(d) / "nope.env"), FileNotFoundError))

    print("token expiry reporting:")
    check("no expiry recorded -> 'unknown'", "unknown" in token_expiry_note({}))
    check("past expiry -> EXPIRED",
          "EXPIRED" in token_expiry_note({"CTRADER_TOKEN_EXPIRES_AT":
                                          str(int(time.time()) - 10)}))
    check("future expiry -> a duration",
          "expires in" in token_expiry_note({"CTRADER_TOKEN_EXPIRES_AT":
                                             str(int(time.time()) + 86400 * 3)}))

    print("error mapping:")

    class _Err:
        errorCode = "ACCESS_DENIED"
        description = "not permitted"
    _Err.__name__ = "ProtoOAErrorRes"
    check("ProtoOAErrorRes raises FTMOError",
          _raises_with(lambda: _raise_if_error(_Err()), FTMOError))

    class _Ok:
        pass
    _Ok.__name__ = "ProtoOATraderRes"
    check("a normal response does not raise", _raise_if_error(_Ok()) is None)

    print("\nFAILED" if failures else "\nAll ftmo_service offline selftests passed.")
    return 1 if failures else 0


def _raises(fn) -> bool:
    try:
        fn()
    except Exception:
        return True
    return False


def _raises_with(fn, exc) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO venue adapter (cTrader Open API).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--authorize", action="store_true",
                   help="One-time OAuth in the browser; writes tokens to .env.")
    g.add_argument("--refresh", action="store_true",
                   help="Exchange the refresh token for a fresh access token.")
    g.add_argument("--probe", action="store_true",
                   help="Connect read-only: list accounts, balance, positions, symbols.")
    g.add_argument("--selftest", action="store_true",
                   help="Offline checks; no network, no credentials needed.")
    args = ap.parse_args()

    if args.selftest or not any((args.authorize, args.refresh, args.probe)):
        return selftest()
    if args.authorize:
        authorize()
        return 0
    if args.refresh:
        refresh_tokens()
        print("Access token refreshed and written to .env.")
        return 0
    return probe()


if __name__ == "__main__":
    sys.exit(main())
