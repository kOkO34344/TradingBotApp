---
tags: [infrastructure, broker, execution]
status: "live and trading"
connection_verified: 2026-07-21
last_updated: 2026-07-28
---

# IBKR Integration: Connection & Execution

## Status

✅ **Live and trading** — Paper account `DUQ903866`, IB Gateway port 4002. Connection verified 2026-07-21; `paper_trader.py` built and executed a real rebalance the same day. See "Live Execution" below.

## Connection

### Setup (one-time)

1. Download and install **IB Gateway** (or use existing TWS)
   - Gateway is lighter-weight; TradingBotApp uses Gateway
   - Download: https://www.interactivebrokers.com/en/trading/ib-gateway

2. Log in to your **paper account** (starts with 'D', e.g., `DU1234567`)

3. Enable the API:
   - **Gateway:** File → Settings → API Settings
   - **TWS:** Edit → Global Configuration → API → Settings
   - Check "Enable ActiveX and Socket Clients"
   - Select socket port: **4002** (Gateway paper) or **7497** (TWS paper)

4. Note your paper account ID and chosen port

### Configuration

In `trader_settings.json`:
```json
{
  "ibkr_port": 4002,
  "ibkr_client_id": 9
}
```

- `ibkr_port`: **Must be a paper port (7497 or 4002).** The code refuses live ports (7496, 4001) unless `allow_live=True` is passed — a deliberate safety bump.
- `ibkr_client_id`: Any small integer, unique per connected app (so multiple processes don't collide)

### Connection Test

```bash
python3 ibkr_service.py
```

**Expected output:**
```
Connected. Paper account verified: DU1234567
AAPL — last 5 bars (15min):
  [DataFrame with Close, High, Low, Open, Volume]
EURUSD — last 5 bars (15min):
  [DataFrame]
BTC — last 5 bars (15min):
  [DataFrame]
```

If it fails, check:
- Is Gateway/TWS running and logged in?
- Is the API socket port correct in settings?
- Is the paper account ID starting with 'D'?
- Are there any firewall blocks on localhost:4002?

## Execution Layer

### Contract Types

Built-in builders for all major asset classes:

```python
from ibkr_service import stock, forex_pair, future, crypto, place_bracket_order

# Equities
contract = stock("AAPL")  # or stock("NVDA", "USD")

# Forex
contract = forex_pair("EURUSD")

# Futures (commodities are just futures)
contract = future("MGC", "202612", "COMEX")  # Micro Gold, Dec 2026
contract = future("ES", "202612", "CME")    # S&P 500 E-mini, Dec 2026
contract = future("CL", "202612", "NYMEX")  # Crude Oil, Dec 2026

# Crypto
contract = crypto("BTC")  # or crypto("ETH", "USD")
```

### Order Types

**Preferred: Bracket Order** (limit entry + stop, atomic)

```python
trades = place_bracket_order(
    ib, 
    stock("AAPL"), 
    quantity=50, 
    action="BUY",
    entry_limit=189.50,
    stop_price=185.00,
    target_price=200.00
)
```

- Entry, stop, and optional target placed atomically
- Ensures a position can't exist without a stop
- Both stop and target inherit the position's quantity (so if 50 shares filled, stop is for 50)
- Passes through RiskGuard before submission

**Fallback: Market Order** (use sparingly)

```python
trade = place_market_order(
    ib, 
    stock("AAPL"), 
    quantity=50, 
    action="BUY",
    allow_no_stop=True  # Acknowledge you're skipping a stop
)
```

- Filled immediately at market
- **Blocked by default if no stop is attached** — have to pass `allow_no_stop=True`
- Useful for exiting on urgent signals, not for entering

### Data

**Pull recent bars:**

```python
from ibkr_service import get_15min_bars
df = get_15min_bars(ib, contract, duration="2 D")
# Returns: DataFrame with index=date, columns=[Open, High, Low, Close, Volume]
```

**Stream live updates:**

```python
from ibkr_service import stream_15min_bars
bars = stream_15min_bars(ib, contract, on_update=my_callback)
ib.run()  # Keep event loop alive
```

The bot doesn't use streaming yet — `paper_trader.py` pulls fresh daily data (via `yfinance`, force-refetched) for the momentum signal and a live delayed quote (via `market_price()`) for sizing/entry, rather than subscribing to a 15-min stream.

## Live Execution — `paper_trader.py`

**Built and run for real 2026-07-21.** Flow: fresh momentum signal (top-N of watchlist) → diff against `ib.positions()` → printed proposal → explicit `y/n` → execute (exits first, then entries) → everything journaled.

**First rebalance:** bought GOOGL (14 sh @ ~349.62 avg), AAPL (15 sh @ ~328.04 avg), JNJ (19 sh @ ~249.98 avg) — all sized from RiskGuard's risk budget (`qty = floor(NetLiq_usd * risk_pct_per_trade% / (2*ATR))`, clamped to `max_order_notional_usd`).

**Two account quirks handled:**
- The paper account is **EUR-denominated** (`NetLiquidation` reports in EUR) with no USD segment at all. `paper_trader.get_net_liquidation_usd()` converts to USD — see the 2026-07-25 update below for how this changed.
- No live market-data subscription on this paper account — `market_price()` calls need `ib.reqMarketDataType(3)` (delayed data) first, or they raise "requires additional subscription."

**⚠️ Update 2026-07-25: EUR→USD conversion no longer uses a live FX quote.**
It used to call `market_price(forex_pair("EURUSD"))`, which needs an active
market-data line — and that line isn't guaranteed. Hit in practice: IBKR
error 10197 "No market data during competing live session" (something else
was logged into the same account, holding the data line), which broke
`paper_trader.py --dry-run` outright and would have broken **every hourly
autotrade firing** the same way. Converts now via IBKR's own `ExchangeRate`
account value instead — that arrives on the account channel, needs no
subscription and no data line at all.

`ExchangeRate` for currency C is the value of 1 C in the account's BASE
currency, so `USD = BASE / rate_usd`. That direction is not assumed — it's
checked at runtime against an independent yfinance `{BASE}USD=X` quote and
**raises** on mismatch, because getting it backwards misstates equity by
~29% on this account (1.137 vs 0.879) and would silently mis-size every
order. (IBKR's own cash-balance identity — `sum(cash_C * rate_C) ==
cash_BASE` — was tried as the check first and rejected: tested against a
deliberately inverted rate, it still reconciled to within 0.26%, well inside
any tolerance loose enough to survive normal rounding. The yfinance
cross-check separates the two hypotheses by 29%, not 0.3%.)

**⚠️ Safety bug found and fixed same day: stops defaulting to DAY, not GTC.**
`place_bracket_order`'s stop leg didn't set a TIF, so IBKR defaulted it to `DAY` — a completely reasonable-looking `PreSubmitted` stop that silently **expires at end of the trading session**. All three positions above were briefly unprotected until this was caught (re-checking positions later showed the stops as `Cancelled` in `ib.trades()`, with zero executions — ruling out an actual stop-out). Fixed by setting `tif="GTC"` on the stop (and target, if used) legs in `ibkr_service.py`, then manually re-protecting all three positions with fresh GTC stops (journaled as "re-protect" entries in `trade_journal.csv`).

**Operational lesson:** when checking a position's protection, checking `ib.openTrades()` minutes after placing isn't enough — a DAY order looks identical to a GTC one until end-of-session. Always check `order.tif == "GTC"` explicitly, and use `ib.trades()` (not just `openTrades()`) to see orders that have since been cancelled/expired.

**⚠️ 2026-07-23/25: a GTC stop firing correctly still went unrecorded.** GOOGL's
GTC stop worked exactly as designed — but the position gap-opened through it
(07-23 opened at 321.13, below the 326.06 stop), and the close reached
neither `trade_journal.csv` nor a Telegram alert until an audit caught it
2026-07-25. This was a *detection* gap, not a stop-mechanism gap — see
[[Risk Management System]]'s "residual risk" note (which predicted almost
exactly this) for the fix (`reflect_on_trades.py`'s two-tier detection).

**⚠️ 2026-07-25: connections now distinguish read-only from read/write at the
socket, not just by convention.** `ibkr_service.connect()` gained a
`readonly` parameter (`ib_async`'s `IB.connect(readonly=True)`, which makes
TWS/Gateway itself reject order placement on that connection). Three
call sites previously *claimed* "read-only" in their docstrings/help text
while opening a normal read/write connection — `paper_trader.py --dry-run`,
`reflect_on_trades.py`, and the new watchlist position-check — all now pass
`readonly=True` for real. Default stays `False`, so no trading path changed.

## Asset-Class Specifics

### Data type quirks

IBKR requires different `whatToShow` types per asset:
- **Stocks, futures:** `TRADES` (default)
- **Forex:** `MIDPOINT` (forex has no trade tape, only bid/ask)
- **Crypto:** `AGGTRADES` (aggregated trades via Paxos)

This is handled automatically in `_what_to_show(contract)` — no configuration needed.

### Margin & PDT

- **Paper account:** Unlimited margin, no PDT rules
- **Live account:** Pattern Day Trader rule limits day trades to 3 per 5 rolling days if under $25k. This project is swing/position trading (multi-day holds), so it's not as constrained, but keep this in mind for later.

### Futures Contract Expiry

Contracts like `future("MGC", "202612", "COMEX")` expire after their month. Before expiry, code needs to roll to the next month's contract (e.g., 202612 → 202703). **Not yet automated** — flagged for Phase 3 work if using futures actively. For now, manually update the contract symbol in settings/code before expiry.

## Security & Separation

- **Keys stored locally in IB Gateway/TWS** — not in code, not in config files
- **No API credentials in git** — only port/client_id, which are harmless
- **Paper account verification in code** — `verify_paper_account()` checks account ID starts with 'D' and refuses to proceed otherwise (unless `allow_live=True`)
- **No live-port connections** — `connect()` refuses ports 7496/4001 unless explicitly overridden

## Testing

**Offline self-test (no TWS needed):**
```bash
python3 ibkr_service.py --selftest
```

**Connected smoke test (requires Gateway/TWS + paper account):**
```bash
python3 ibkr_service.py
```

Both are included in the file; run before committing any code changes.

## Related Notes

- [[Risk Management System]] — the RiskGuard that sits in front of all orders
- [[Trade Journal Structure]] — how order attempts/fills/blocks are logged
- [[Plan]] — Phase 3 (paper trading), Phase 4 (tiny live capital)

## Files

- `ibkr_service.py` — the full connection, contract, and execution layer
- `trader_settings.json` — connection config (port, client_id)
- `risk_limits.json` — enforced limits (auto-created with defaults)
- `trade_journal.csv` — audit trail of all order attempts (created on first order)

## Troubleshooting

| Error | Fix |
|---|---|
| `Connection refused` | Gateway/TWS not running, or wrong port in settings |
| `No managed accounts visible — is the API logged in?` | Log in to Gateway/TWS first |
| `Account DU... does not look like a paper account` | Account doesn't start with 'D', or it's a live account — check IBKR account page |
| `Port 7496 is a LIVE trading port` | You're trying to connect to a live port; change to 7497 (TWS paper) or 4002 (Gateway paper), or pass `allow_live=True` only after months of paper evidence |
| Order gets `BLOCKED: require_stop_attached=true` | This is working as intended — add a stop to the order or edit `risk_limits.json` |
| A stop shows `Cancelled` in `ib.trades()` with no matching fill | Check `order.tif` — if it was `DAY` (pre-2026-07-21 fix), it expired at end of session. Re-protect the position immediately with a fresh GTC stop. |
| `market_price()` raises "requires additional subscription for API" | The account has no live data subscription — call `ib.reqMarketDataType(3)` for delayed data before requesting prices |
| `NetLiquidation`/account values look ~10-15% off from expected USD | The account's base currency isn't USD (this paper account is EUR) — `paper_trader.get_net_liquidation_usd()` converts via IBKR's `ExchangeRate` account value (not a live FX quote, as of 2026-07-25) |
| `No market data during competing live session` (IBKR error 10197) | Another session is holding the data line. No longer breaks NetLiq conversion (fixed 2026-07-25, see above) — but still blocks anything that genuinely needs a live quote (e.g. entry price checks). Close the competing session. |
| `Order TIF was set to DAY based on order preset` (IBKR error **10349**) | ✅ **FIXED 2026-07-28 in code. This was OUR bug, not a Gateway setting — do not send anyone to Global Configuration → Presets.** `place_bracket_order` built the parent `LimitOrder` with **no explicit `tif`**; the Order Preset filled in the blank with DAY and *announced* it. Proved by direct probe: the error's `reqId` is always the **parent's**, never the stop's; the stop leg always carried explicit `tif="GTC"` and IBKR held it as GTC throughout (confirmed via `reqAllOpenOrders`, not the local order object); and it is a **warning, not a rejection** — both legs stayed `PreSubmitted`. Fix: `LimitOrder(action, qty, entry_limit, tif="DAY")`. **General rule: never leave a TIF unset on any order** — an unset field is one the broker's config gets to fill in, and you are not necessarily told what it chose. |
| `ib_async` logs `Canceled order: Trade(...status='Cancelled')` but the order is actually alive | **Do not trust that line.** It contradicted IBKR's own `reqAllOpenOrders` view during the 2026-07-28 probe, which showed both legs `PreSubmitted`. This is very likely what made the 07-27 session conclude a whole rebalance had been cancelled when AMZN and DIS had in fact filled. IBKR's authoritative view is `reqAllOpenOrders`, never a local `Trade` object or a log line. |
| A bracket `RESULT` row says `Cancelled` seconds after placement | Until 2026-07-28 `place_bracket_order` journalled the parent's status after a flat `ib.sleep(1)` — a snapshot, not an outcome. It now waits for a terminal status via `wait_for_status()`, records the real fill/price, and captures **IBKR's own error text** into the `detail` column via `OrderErrorCollector`, so a cancel explains itself. Trust `RESULT_CORRECTED` rows over original `RESULT` rows dated before 2026-07-28. |
| A position exists with no live GTC stop | `place_bracket_order` now calls `verify_stop_protection()` on every fill: it re-requests open orders and confirms a stop covering the full filled quantity with `tif == "GTC"`. If not, it journals `UNPROTECTED` and **texts immediately**. A DAY stop counts as *no* protection, deliberately. It does not auto-place a replacement — that is a human decision. |
| Orders placed while the market is closed just sit there | Expected: `outsideRth` is never set, so all orders are regular-hours-only (09:30-16:00 ET). `paper_trader.py` now warns and asks for confirmation before placing outside those hours, using the shared `ibkr_service.market_is_open()` (moved there 2026-07-28 so the human-approved and unattended paths ask the same code). Note TIF governs how long an *unfilled* order lives — it is not what stops you trading at night. |
| `ib.positions()` returns `[]` on an account that holds positions | The positions request timed out during `IB.connect()` and `connectAsync` **swallowed it** (`asyncio.wait_for(..., timeout=4)` + `return_exceptions=True`, and `raiseSyncErrors` defaults False). The connection looks healthy; the cache is just empty. Never treat an empty `ib.positions()` as "flat" — re-request and let a timeout raise. See [[Risk Management System]] and the tier-2 note in CLAUDE.md. |
