#!/usr/bin/env python3
"""
notify.py — reusable Telegram phone-notification module for TradingBotApp.

Lives in its own TelegramBot/ folder (separate from the strategy/backtest
code) specifically so ANY script anywhere in the project can import it for
ANY kind of alert — not just the Kronos backtest. Current + likely future
callers: the Kronos backtest wrapper, paper_trader.py (fills, circuit
breaker trips), reflect_on_trades.py, the daily vault sync, IBKR
connection-loss alerts, research_agent.py run failures, grade_calls.py
weekly calibration summaries, launchd job failures, etc. One bot, one
chat_id, many message sources.

Credentials live in TelegramBot/.env (never committed — covered by
.gitignore's `*.env` rule), not hardcoded and not passed on the CLI
(shell history / `ps` would leak them).

One-time setup
---------------
1. In Telegram, message @BotFather -> /newbot -> follow the prompts.
   BotFather gives you a token like "123456789:AAExampleTokenString".
2. Send your new bot ANY message (e.g. "hi") from the Telegram app on the
   phone you want notifications on. Bots can't message you first.
3. Run:  python3 notify.py --get-chat-id <TOKEN>
   This calls getUpdates and prints the chat_id tied to the message you
   just sent.
4. Create TradingBotApp/TelegramBot/.env (copy .env.example) with:
       TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenString
       TELEGRAM_CHAT_ID=987654321
5. Test:  python3 notify.py "hello from TradingBotApp"

Usage from ANY other script in the project
--------------------------------------------
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent / "TelegramBot"))  # adjust
                                                                      # depth
                                                                      # to reach
                                                                      # TradingBotApp/
    from notify import send_telegram
    send_telegram("Kronos backtest done: IC 0.14, beat SPY: yes")

Never raises on failure by default — a notification going down shouldn't
crash the backtest/trading script that's reporting on it. Pass
raise_on_error=True if a caller specifically wants to know delivery failed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"
FAILED_LOG = Path(__file__).parent / "failed_sends.log"
API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _load_env() -> dict:
    """Minimal .env parser (KEY=VALUE per line, # comments) — avoids adding
    python-dotenv as a dependency for two variables."""
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _credentials() -> tuple[str, str]:
    env = {**_load_env(), **os.environ}  # real env vars override .env
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram not configured. Create TradingBotApp/TelegramBot/.env "
            "with TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=... "
            "(see notify.py's module docstring for setup steps)."
        )
    return token, chat_id


def send_telegram(text: str, raise_on_error: bool = False, retries: int = 4) -> bool:
    """Send `text` to the configured chat. Returns True on success, False
    on failure (unless raise_on_error=True, in which case it raises).

    Retries with backoff (3s, 6s, 12s, 24s by default) before giving up —
    tuned for brief drops on a flaky connection (e.g. a phone hotspot),
    not for a genuinely offline machine. On final failure, appends to
    failed_sends.log so a silent miss is checkable in one place instead
    of buried in whichever script's own log happened to call this."""
    try:
        token, chat_id = _credentials()
    except RuntimeError as e:
        print(f"[notify] {e}", file=sys.stderr)
        if raise_on_error:
            raise
        return False

    url = API_BASE.format(token=token, method="sendMessage")
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text[:4000],  # Telegram's hard message-length limit
        "disable_web_page_preview": True,
    }).encode()
    req = urllib.request.Request(url, data=payload,
                                  headers={"Content-Type": "application/json"})

    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
                if body.get("ok"):
                    return True
                last_err = body.get("description", "unknown Telegram API error")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(min(3 * (2 ** attempt), 30))

    msg = f"[notify] Failed to send Telegram message after {retries + 1} attempt(s): {last_err}"
    print(msg, file=sys.stderr)
    try:
        with open(FAILED_LOG, "a") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {last_err}  "
                    f"| text: {text[:200]!r}\n")
    except Exception:
        pass  # even the failure log failing shouldn't raise

    if raise_on_error:
        raise RuntimeError(f"Telegram send failed: {last_err}")
    return False


def _get_chat_id(token: str) -> None:
    """One-time helper: calls getUpdates and prints chat_id(s) seen. Run
    this AFTER sending your bot at least one message from Telegram."""
    url = API_BASE.format(token=token, method="getUpdates")
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except Exception as e:
        print(f"Could not reach Telegram API: {e}", file=sys.stderr)
        sys.exit(1)
    if not body.get("ok"):
        print(f"Telegram API error: {body.get('description')}", file=sys.stderr)
        sys.exit(1)
    results = body.get("result", [])
    if not results:
        print("No messages seen yet. Send your bot a message on Telegram "
              "first, then re-run this.")
        return
    seen = {}
    for update in results:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        chat = msg["chat"]
        seen[chat["id"]] = chat.get("username") or chat.get("first_name") or chat.get("title")
    for chat_id, name in seen.items():
        print(f"chat_id={chat_id}   ({name})")


def main():
    ap = argparse.ArgumentParser(description="Send a Telegram notification, or fetch your chat_id.")
    ap.add_argument("message", nargs="?", help="Message text to send")
    ap.add_argument("--get-chat-id", metavar="TOKEN",
                     help="Look up chat_id(s) for TOKEN from recent messages sent to the bot")
    args = ap.parse_args()

    if args.get_chat_id:
        _get_chat_id(args.get_chat_id)
        return

    if not args.message:
        ap.error("Provide a message to send, or use --get-chat-id TOKEN")

    ok = send_telegram(args.message)
    print("Sent." if ok else "Failed to send (see stderr).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
