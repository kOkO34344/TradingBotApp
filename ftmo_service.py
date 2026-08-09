#!/usr/bin/env python3
"""
ftmo_service.py — FTMO venue adapter over the cTrader Open API.

The broker layer for the FTMO venue — the only venue this project trades.
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
The FastAPI backend is asyncio and Twisted is not. They can share a process, but
only via Twisted's asyncio reactor, and it must be installed BEFORE anything
imports the default reactor. `install_asyncio_reactor()` below does that and is
a no-op once a reactor exists. The CLI paths in this file run standalone and
use the default reactor, which is why they are safe today — but anything that
later imports this module into the web backend must call that installer first.

CREDENTIALS live in the gitignored `secrets/ctrader.env` (see
`secrets/ctrader.env.example` for the key names, and `secrets_store.py` for why
they moved and why the old repo-root `.env` still works). They are never
logged, never printed, and never interpolated into a shell command.

ENDPOINT ROUTING IS BY ACCOUNT TYPE, and it is the one thing that will waste
your afternoon. A live-type account authenticates ONLY on the live host and a
demo-type account ONLY on the demo host; the wrong pairing returns a bare
`CANT_ROUTE_REQUEST` immediately after a SUCCESSFUL application auth and a
SUCCESSFUL account list, so it reads like a token problem and is not.
FTMO issues Challenge and Free Trial accounts on its LIVE cTrader server with
SIMULATED capital, so `CTRADER_HOST=live` is correct for an FTMO trial and does
not breach rule 1 — `isLive` is a routing flag, not a claim about real money.
`select_account()` refuses a mismatch before account auth and names the fix.

Usage:
  python3 ftmo_service.py --authorize     # one-time OAuth, writes tokens to .env
  python3 ftmo_service.py --refresh       # new access token from the refresh token
  python3 ftmo_service.py --probe         # connect, list accounts, show state
  python3 ftmo_service.py --symbols       # capture real symbol specs to JSON
  python3 ftmo_service.py --selftest      # offline checks, no network
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).parent
SPECS_FILE = BASE_DIR / "ftmo_symbol_specs.json"

# Credentials live under secrets/ (see secrets_store.py). resolve() falls back
# to the old repo-root .env when the migration has not been applied to this
# checkout, so an unmigrated clone keeps working rather than failing at auth.
import secrets_store  # noqa: E402  (after BASE_DIR, before it is used)

ENV_FILE = secrets_store.resolve("ctrader")

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


def host_choice(env: dict) -> str:
    """'demo' or 'live' — the configured endpoint name, validated.

    Unset defaults to 'demo' and a typo raises, both in the safe direction:
    nothing should ever reach the live endpoint by falling through a default.
    """
    choice = (env.get("CTRADER_HOST") or "demo").strip().lower()
    if choice not in ("demo", "live"):
        raise ValueError(f"CTRADER_HOST must be 'demo' or 'live', got {choice!r}")
    return choice


def host_for(env: dict) -> str:
    from ctrader_open_api import EndPoints
    return (EndPoints.PROTOBUF_DEMO_HOST if host_choice(env) == "demo"
            else EndPoints.PROTOBUF_LIVE_HOST)


def routing_hint(choice: str, is_live: bool) -> str | None:
    """An actionable message when the endpoint cannot serve this account type.

    cTrader routes by endpoint: a live-type account authenticates ONLY on the
    live host and a demo-type account ONLY on the demo host. Sending
    `ProtoOAAccountAuthReq` to the wrong one returns a bare
    `CANT_ROUTE_REQUEST: Cannot route request` which names neither the account
    nor the endpoint, and reads like a permissions or token problem. Hit
    2026-08-05 on the first probe after the app went Active.

    `isLive` is the account's cTrader ROUTING TYPE and is NOT evidence that
    real money is at stake. FTMO issues its Challenge and Free Trial accounts
    on its LIVE cTrader server with simulated capital, so an FTMO trial is
    legitimately live-type — see rule 9 in CLAUDE.md. Returns None when the
    pairing is fine.
    """
    needed = "live" if is_live else "demo"
    if choice == needed:
        return None
    return (f"CTRADER_HOST is '{choice}', but this account is {needed}-type and "
            f"is reachable only on the '{needed}' endpoint. Set "
            f"CTRADER_HOST={needed} in .env and re-run. (FTMO Challenge and "
            f"Free Trial accounts are live-type with SIMULATED capital — "
            f"live-type is not real money. See rule 9.)")


def install_asyncio_reactor() -> bool:
    """Make Twisted run on the asyncio event loop. No-op if already installed.

    Only matters when this module is imported alongside FastAPI.
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


# Every payload type that means "this failed". ProtoOAOrderErrorEvent is the
# one that matters most and is the easiest to miss: a REJECTED ORDER comes back
# as an *event*, not as a ProtoOAErrorRes, so a handler that only knows about
# the Res types treats a rejection as success. That happened on the first live
# FTMO order (2026-08-05) — the venue refused it and the code reported
# {'sent': True}. Only the smoke test's own read-back caught it.
_ERROR_PAYLOADS = ("ProtoOAErrorRes", "ProtoErrorRes", "ProtoOAOrderErrorEvent")


def _raise_if_error(payload) -> None:
    name = type(payload).__name__
    if name in _ERROR_PAYLOADS:
        detail = getattr(payload, "description", "") or ""
        order_id = getattr(payload, "orderId", 0)
        where = f" (orderId {order_id})" if order_id else ""
        raise FTMOError(f"{getattr(payload, 'errorCode', '?')}: {detail}{where}")


def select_account(accounts, configured: str, choice: str):
    """Pick which account to use, and refuse an endpoint/type mismatch.

    Pure and offline-testable: `accounts` only needs `ctidTraderAccountId` and
    `isLive`, so the selection rules are covered by the selftest without a
    connection. Every network mode routes through this, so none of them can
    disagree about which account they are talking to.
    """
    if not accounts:
        raise FTMOError("No trading accounts are visible for this access token.")
    configured = (configured or "").strip()
    if configured:
        if not configured.isdigit():
            raise FTMOError(
                f"CTRADER_ACCOUNT_ID must be a number, got {configured!r}.")
        target = int(configured)
        selected = next((a for a in accounts
                         if a.ctidTraderAccountId == target), None)
        if selected is None:
            visible = ", ".join(str(a.ctidTraderAccountId) for a in accounts)
            raise FTMOError(
                f"CTRADER_ACCOUNT_ID={target} is not among the accounts this "
                f"access token can see ({visible}).")
    else:
        selected = accounts[0]
        target = selected.ctidTraderAccountId
    hint = routing_hint(choice, bool(getattr(selected, "isLive", False)))
    if hint:
        raise FTMOError(hint)
    return target, selected


def _enum_name(wrapper, value, default: str = "?") -> str:
    """Render a protobuf enum as its name. Never raises — this is only ever
    used for reporting, and an unknown id must not abort a read-only probe."""
    try:
        return wrapper.Name(value)
    except Exception:
        return default if value is None else f"{default}({value})"


def probe(env: dict | None = None, timeout_s: int = 45) -> int:
    """Connect, authenticate, list accounts and report account state.

    This is the "does the whole path actually work" check — it places nothing
    and changes nothing. Everything it prints comes from the venue.
    """
    from twisted.internet import reactor, defer
    from ctrader_open_api import Client, Protobuf, TcpProtocol
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model

    env = env or load_env()
    client_id, client_secret, access_token = require(
        env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN")
    host = host_for(env)
    choice = host_choice(env)
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

            # brokerName is NOT on ProtoOACtidTraderAccount — it lives on
            # ProtoOATrader, fetched below. Printing it here always rendered
            # "broker=?" and looked like the venue withholding data.
            print(f"\n{len(accounts)} trading account(s) visible:")
            for a in accounts:
                kind = "live-type" if getattr(a, "isLive", False) else "demo-type"
                print(f"  ctidTraderAccountId={a.ctidTraderAccountId}  {kind}  "
                      f"login={getattr(a, 'traderLogin', '?')}")

            target, _ = select_account(accounts, configured_account, choice)
            if not configured_account:
                print(f"\nCTRADER_ACCOUNT_ID is unset — probing the first account "
                      f"({target}).")

            res = yield client.send("ProtoOAAccountAuthReq",
                                    ctidTraderAccountId=target,
                                    accessToken=access_token)
            _raise_if_error(Protobuf.extract(res))
            print(f"account {target} authenticated")

            # Asset names, so the deposit currency reads "USD" rather than the
            # bare id 15. The sizer's quote_to_account_rate is meaningless
            # unless we know what the account currency actually is.
            assets = {}
            try:
                res = yield client.send("ProtoOAAssetListReq",
                                        ctidTraderAccountId=target)
                payload = Protobuf.extract(res)
                _raise_if_error(payload)
                assets = {a.assetId: a.name for a in payload.asset}
            except Exception as e:  # non-fatal: it only affects labelling
                print(f"  (asset list unavailable: {type(e).__name__})")

            res = yield client.send("ProtoOATraderReq", ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            t = payload.trader
            digits = getattr(t, "moneyDigits", 2) or 2
            scale = float(10 ** digits)
            balance = t.balance / scale
            deposit_id = getattr(t, "depositAssetId", None)
            currency = assets.get(deposit_id, f"assetId {deposit_id}")
            rights = _enum_name(model.ProtoOAAccessRights,
                                getattr(t, "accessRights", None))
            print(f"\naccount state:")
            print(f"  broker            {getattr(t, 'brokerName', '?')}")
            print(f"  balance           {balance:,.2f} {currency}")
            print(f"  leverage          {getattr(t, 'leverageInCents', 0) / 100:.0f}x")
            print(f"  account type      "
                  f"{_enum_name(model.ProtoOAAccountType, getattr(t, 'accountType', None))}")
            # FULL_ACCESS is the only value under which the order path can work.
            # CLOSE_ONLY / NO_TRADING would let every pre-trade check pass and
            # then fail at the venue, which is the worst place to discover it.
            print(f"  access rights     {rights}"
                  f"{'' if rights == 'FULL_ACCESS' else '   <-- NOT FULL_ACCESS'}")
            print(f"  money digits      {digits}")

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


def fetch_symbol_specs(env: dict | None = None, timeout_s: int = 120,
                       out_path: Path | None = None) -> int:
    """Capture REAL ProtoOASymbol specs from the venue into a tracked JSON file.

    WHY THIS EXISTS. `ftmo_sizing.py` needs min/step/max volume, digits and lot
    size per symbol to turn a risk budget into an order volume, and until now
    its tests used plausible but INVENTED numbers. An invented step size is
    precisely the kind of thing that passes every test and then places a
    wrongly-sized order at the venue — the sizer's own docstring notes that
    flooring to the step is what keeps risk under budget, and that guarantee is
    only as good as the step being real.

    The capture is written to disk and tracked in git so the sizer's selftest
    can assert against real venue values while staying OFFLINE and
    credential-free, the same reasoning that makes `grading_cache.json`
    tracked: a file the risk maths is validated against must not change
    depending on whether the network was up.

    `ProtoOASymbolsListReq` alone is NOT enough — it returns ProtoOALightSymbol
    (id, name, category, asset ids) with no volume or lot data at all. The
    numbers that matter only come from `ProtoOASymbolByIdReq`.

    Read-only. Lists symbols and asks for their specs; places nothing.
    """
    from twisted.internet import reactor, defer
    from ctrader_open_api import Client, Protobuf, TcpProtocol
    from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model

    env = env or load_env()
    client_id, client_secret, access_token = require(
        env, "CTRADER_CLIENT_ID", "CTRADER_CLIENT_SECRET", "CTRADER_ACCESS_TOKEN")
    host = host_for(env)
    choice = host_choice(env)
    configured_account = (env.get("CTRADER_ACCOUNT_ID") or "").strip()
    out_path = out_path or SPECS_FILE

    print(f"cTrader Open API -> {host}:5035  (token {token_expiry_note(env)})")
    state = {"rc": 1}

    @defer.inlineCallbacks
    def run(client):
        try:
            res = yield client.send("ProtoOAApplicationAuthReq",
                                    clientId=client_id, clientSecret=client_secret)
            _raise_if_error(Protobuf.extract(res))

            res = yield client.send("ProtoOAGetAccountListByAccessTokenReq",
                                    accessToken=access_token)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            target, _ = select_account(list(payload.ctidTraderAccount),
                                       configured_account, choice)

            res = yield client.send("ProtoOAAccountAuthReq",
                                    ctidTraderAccountId=target,
                                    accessToken=access_token)
            _raise_if_error(Protobuf.extract(res))
            print(f"account {target} authenticated")

            res = yield client.send("ProtoOAAssetListReq",
                                    ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            assets = {a.assetId: a.name for a in payload.asset}

            res = yield client.send("ProtoOASymbolsListReq",
                                    ctidTraderAccountId=target)
            payload = Protobuf.extract(res)
            _raise_if_error(payload)
            light = {s.symbolId: s for s in payload.symbol}
            print(f"{len(light)} symbols listed; fetching full specs")

            # Batched: symbolId is a repeated field, and one request per symbol
            # would be 202 round trips into cTrader's rate limiter.
            specs = {}
            ids = sorted(light)
            for i in range(0, len(ids), 100):
                chunk = ids[i:i + 100]
                res = yield client.send("ProtoOASymbolByIdReq",
                                        ctidTraderAccountId=target,
                                        symbolId=chunk)
                payload = Protobuf.extract(res)
                _raise_if_error(payload)
                for s in payload.symbol:
                    specs[s.symbolId] = s
                print(f"  specs {len(specs)}/{len(ids)}")

            records = {}
            for sid in ids:
                s, l = specs.get(sid), light[sid]
                if s is None:
                    continue
                records[l.symbolName] = {
                    "symbol_id": sid,
                    "digits": s.digits,
                    "pip_position": s.pipPosition,
                    "lot_size": s.lotSize,
                    "min_volume": s.minVolume,
                    "step_volume": s.stepVolume,
                    "max_volume": s.maxVolume,
                    "trading_mode": _enum_name(model.ProtoOATradingMode,
                                               s.tradingMode),
                    "enable_short_selling": bool(s.enableShortSelling),
                    # The venue's own trading calendar. Captured because a
                    # STREAMING QUOTE DOES NOT MEAN A TRADEABLE MARKET: on
                    # 2026-08-05 US30.cash and BTCUSD both quoted happily and
                    # both rejected an order with MARKET_CLOSED. Note the zone
                    # is the SYMBOL's (Europe/Moscow here), which is NOT the
                    # Europe/Prague boundary ftmo_rules uses for the FTMO day —
                    # two different timezones in one system, do not conflate.
                    "schedule_timezone": getattr(s, "scheduleTimeZone", ""),
                    "schedule": [{"start": iv.startSecond, "end": iv.endSecond}
                                 for iv in s.schedule],
                    "base_asset": assets.get(l.baseAssetId, ""),
                    "quote_asset": assets.get(l.quoteAssetId, ""),
                    "category_id": l.symbolCategoryId,
                    "description": l.description,
                }

            doc = {
                "_meta": {
                    "captured_utc": datetime.now(timezone.utc)
                                            .strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "host": choice,
                    "ctid_trader_account_id": target,
                    "symbol_count": len(records),
                    "note": ("Real venue specs, captured read-only. Volumes are "
                             "in cTrader centi-units exactly as reported. "
                             "Regenerate with: python3 ftmo_service.py "
                             "--symbols"),
                },
                "symbols": dict(sorted(records.items())),
            }
            out_path.write_text(json.dumps(doc, indent=2) + "\n")
            print(f"\nwrote {len(records)} specs to {out_path.name}")
            state["rc"] = 0
        except Exception as e:
            print(f"\nSPEC FETCH FAILED: {type(e).__name__}: {e}", file=sys.stderr)
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


def load_symbol_specs(path: Path | None = None) -> dict:
    """Read the captured venue specs. Raises if the capture is missing.

    Callers get a clear instruction rather than a KeyError, because the file
    is generated and a fresh clone will not have re-run the capture.
    """
    path = path or SPECS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `python3 ftmo_service.py --symbols` "
            f"once against the venue to capture real symbol specs.")
    return json.loads(path.read_text())["symbols"]


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

    class _OrderErr:
        errorCode = "TRADING_BAD_VOLUME"
        description = "volume is invalid"
        orderId = 4242
    _OrderErr.__name__ = "ProtoOAOrderErrorEvent"
    check("a REJECTED ORDER raises — it arrives as an Event, not a Res, and "
          "treating it as success reported a refused order as sent",
          _raises_with(lambda: _raise_if_error(_OrderErr()), FTMOError))
    check("...and the message carries the venue's own reason",
          "volume is invalid" in _message(lambda: _raise_if_error(_OrderErr())))

    print("endpoint/account-type routing:")
    check("live host + live account is fine", routing_hint("live", True) is None)
    check("demo host + demo account is fine", routing_hint("demo", False) is None)
    check("demo host + live account is refused with the fix named",
          "CTRADER_HOST=live" in (routing_hint("demo", True) or ""))
    check("live host + demo account is refused with the fix named",
          "CTRADER_HOST=demo" in (routing_hint("live", False) or ""))
    check("the hint says live-type is not real money",
          "SIMULATED" in (routing_hint("demo", True) or ""))

    print("host_choice normalises before host_for uses it:")
    check("unset -> demo", host_choice({}) == "demo")
    check("whitespace and case tolerated",
          host_choice({"CTRADER_HOST": "  LIVE "}) == "live")
    check("a typo raises rather than defaulting",
          _raises(lambda: host_choice({"CTRADER_HOST": "liv"})))

    print("select_account:")

    class _Acct:
        def __init__(self, i, live):
            self.ctidTraderAccountId, self.isLive = i, live

    demo_a, live_a = _Acct(11, False), _Acct(22, True)
    check("unset id picks the first account",
          select_account([live_a, demo_a], "", "live")[0] == 22)
    check("a configured id is honoured over ordering",
          select_account([live_a, _Acct(33, True)], "33", "live")[0] == 33)
    check("an id the token cannot see is refused",
          _raises_with(lambda: select_account([live_a], "99", "live"), FTMOError))
    check("...and the error lists what IS visible",
          "22" in _message(lambda: select_account([live_a], "99", "live")))
    check("a non-numeric id is refused, not coerced",
          _raises_with(lambda: select_account([live_a], "abc", "live"), FTMOError))
    check("an empty account list is refused",
          _raises_with(lambda: select_account([], "", "live"), FTMOError))
    check("a type/endpoint mismatch is refused before AccountAuth is sent",
          _raises_with(lambda: select_account([live_a], "", "demo"), FTMOError))
    check("...naming the endpoint to switch to",
          "CTRADER_HOST=live" in _message(
              lambda: select_account([live_a], "", "demo")))

    print("symbol spec capture:")
    with tempfile.TemporaryDirectory() as d:
        missing = Path(d) / "nope.json"
        check("a missing capture tells you how to make one",
              "--symbols" in _message(lambda: load_symbol_specs(missing)))
        good = Path(d) / "specs.json"
        good.write_text(json.dumps(
            {"_meta": {}, "symbols": {"EURUSD": {"symbol_id": 1,
                                                 "min_volume": 100}}}))
        loaded = load_symbol_specs(good)
        check("captured specs load by symbol name",
              loaded["EURUSD"]["min_volume"] == 100)

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


def _message(fn) -> str:
    """The text of whatever `fn` raises, so a test can assert an error is
    ACTIONABLE and not merely present."""
    try:
        fn()
    except Exception as e:
        return str(e)
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="FTMO venue adapter (cTrader Open API).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--authorize", action="store_true",
                   help="One-time OAuth in the browser; writes tokens to .env.")
    g.add_argument("--refresh", action="store_true",
                   help="Exchange the refresh token for a fresh access token.")
    g.add_argument("--probe", action="store_true",
                   help="Connect read-only: list accounts, balance, positions, symbols.")
    g.add_argument("--symbols", action="store_true",
                   help="Capture real symbol specs to ftmo_symbol_specs.json (read-only).")
    g.add_argument("--selftest", action="store_true",
                   help="Offline checks; no network, no credentials needed.")
    args = ap.parse_args()

    modes = (args.authorize, args.refresh, args.probe, args.symbols)
    if args.selftest or not any(modes):
        return selftest()
    if args.authorize:
        authorize()
        return 0
    if args.refresh:
        refresh_tokens()
        print("Access token refreshed and written to .env.")
        return 0
    if args.symbols:
        return fetch_symbol_specs()
    return probe()


if __name__ == "__main__":
    sys.exit(main())
