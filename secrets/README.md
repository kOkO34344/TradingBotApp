# secrets/

Every credential this project uses, one file per provider. **Nothing in here
with a real value is tracked** — `.gitignore` ignores `secrets/*` and re-admits
only this README and the `*.example` templates.

| file | used by | how to get the values |
|---|---|---|
| `ctrader.env` | `ftmo_service.py` (FTMO venue) | `python3 ftmo_service.py --authorize` writes the tokens; the client id/secret come from openapi.ctrader.com/apps |
| `telegram.env` | `TelegramBot/notify.py` (all phone alerts) | message `@BotFather` for the token, then `python3 TelegramBot/notify.py --get-chat-id` |

Set one up by copying the template and filling it in:

```
cp secrets/ctrader.env.example secrets/ctrader.env
chmod 600 secrets/ctrader.env
```

The directory is mode `700` and the files mode `600`. `ftmo_service.py`
re-applies `600` every time it writes a token, so re-authorising cannot loosen
them.

## Where the code looks

`secrets_store.py` is the only module that knows these paths. It resolves
`secrets/<provider>.env` first and falls back to the historical location
(`.env` at the repo root, `TelegramBot/.env`) when the new file is absent.

That fallback is deliberate and worth keeping. Both consumers sit on
**unattended** paths — `notify.py` is how every launchd job reports, and
`ftmo_service.py` is the trading venue adapter — so a half-applied migration
must degrade to "still works", never to "no notifications and nobody notices".
When both exist, the file in here wins, so a forgotten legacy copy cannot
quietly shadow the real one.

Check what this machine holds, without printing any value:

```
python3 secrets_store.py --describe
```

## Audit note

Verified 2026-08-05 against the **full** git history: no credential from either
file has ever been committed, and no value from either appears in any tracked
file. This directory reorganised where they live; it was not cleaning up a
leak.

If a credential is ever exposed, moving the file is not the fix — **rotate it
at the provider**. A value that reached a commit is in every clone and in the
reflog, and rewriting history does not recall it.
