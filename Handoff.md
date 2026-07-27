# TradingBotApp — Handoff (2026-07-28, ~02:00 local)

For Koko, and for whichever session picks this up next. Written at the end of
a long session; everything below was verified against the live paper account,
not inferred.

---

## 1. Where things actually stand

**Account DUQ903866 (paper), verified read-only at 01:50 local:**

| Symbol | Qty | Avg cost | Stop | TIF | Status |
|---|---|---|---|---|---|
| AAPL | 15 | 328.04 | 309.10 | GTC | PreSubmitted |
| JNJ | 19 | 249.98 | 237.61 | GTC | PreSubmitted |
| DIS | 52 | 95.39 | 90.83 | GTC | PreSubmitted |
| AMZN | 21 | 232.73 | 217.74 | GTC | PreSubmitted |

Four positions, every one covered by a full-size GTC stop. Zero leftover
orders from tonight's diagnostic probes.

- **Signal:** Kronos, everywhere. Momentum is gated off in code (`signal_policy.py`).
- **Autotrade:** still `enabled: false`. Deliberate — not turned on tonight.
- **Immediate next action:** supervised Kronos rebalance at the next open,
  **16:30 local / 09:30 ET**. `./trader_app.sh` for the app; the rebalance is
  `.venv/bin/python paper_trader.py` (add `--dry-run` first to preview).
- **Grading:** 0 real graded calls, 76 pending. First 5d grades land ~07-29.

**Two beliefs that were wrong and are now corrected — don't re-inherit them:**
1. The 07-27 rebalance did *not* place zero trades. DIS and AMZN filled.
2. The "Gateway Order Preset blocker" never existed. 10349 was our own missing
   `tif` on the parent order. Nothing needs changing in Gateway.

**Tonight's commits** (all on `main`, from `9a51cb1`):
```
31a61ff Journal the outcome of a bracket, not a one-second snapshot of it
d329f6a Make Kronos the project's signal and put momentum behind a hard gate
326d7b9 Launch the app under .venv so Kronos can find torch
052b5a5 Re-grade the calls; the only 4 "grades" on record were synthetic
ef1e6f0 Share the market-hours check; record that Kronos top-3 is not stable
5dfde25 Capture IBKR's own error text so a cancel says why
edd69bc Set an explicit TIF on the bracket parent; 10349 was ours, not Gateway's
```

---

## 2. The three open risks, and what to do about them

### The theme worth seeing first

All three are the same defect wearing different clothes: **nothing in this
system compares what it INTENDED to happen against what actually happened.**

That is also the root of every incident this project has had —
the 07-23 GOOGL close nobody recorded, the 07-25 phantom liquidation, the
07-27 phantom cancellations. Each was caught by a human eventually noticing a
discrepancy, never by the system. Fixing the three risks individually is
worth doing; building the reconciliation layer fixes the *class*.

Recommendation: do R1 and R3 first (cheap, high value, no strategy change),
then the reconciliation layer, and treat R2 as research rather than a patch.

---

### R1 — Gateway stops answering, and the monitors die quietly

**What's actually broken.** Two distinct failure modes, often conflated:
- *Port closed* → `ConnectionRefused`. Seen 5 times across 07-26/07-27 in
  `trade_reflect.log`.
- *Port open, Gateway unresponsive* → connect hangs indefinitely. Seen
  2026-07-27 after a run of connects with distinct client_ids.

`reflect_on_trades.main()` calls `ibs.connect(...)` **outside any try/except**
(`reflect_on_trades.py:341`). A refused connection kills the script. The
`.sh` wrapper logs the traceback and exits 0. **No Telegram is sent.** So the
close-detection monitor — the thing that exists specifically to catch
unattended stop-outs — can be dead for days and look exactly like a quiet
market. It already was, for most of 07-26 and 07-27.

**Plan:**

1. **Consecutive-failure alerting** (do this first, ~30 min).
   Wrap the connect in `reflect_on_trades.main()`. Keep a small state file
   (`trade_reflections/.connect_failures.json`) counting consecutive
   failures. Text on the **2nd** consecutive failure (one blip on a laptop
   sleep is noise; two in a row at 30-minute spacing is a dead monitor), then
   stay quiet until it recovers, then text the recovery. Never alert on every
   cycle — that trains you to ignore it.
   Reuse the existing convention: conditional `send_telegram()` inside the
   script at the actual event, never a blanket wrapper (CLAUDE.md is explicit
   about this).

2. **Distinguish the two failure modes in the message.** "Gateway not
   listening on 4002" and "Gateway accepted the socket but never answered"
   need different human responses — the first is "start Gateway", the second
   is "kill stray python processes and restart Gateway". A plain
   `socket.connect_ex` probe before `ib.connect()` tells them apart cheaply.

3. **Bound the hang.** `ibs.connect()` should take an explicit timeout and
   raise rather than block forever. Note the trap already documented in
   CLAUDE.md: `ib_async` swallows startup-request timeouts unless
   `raiseSyncErrors=True`. A connection that "succeeds" with an empty
   position cache is worse than one that fails.

4. **Surface it in the daily digest.** One line: last successful IBKR contact,
   and how long ago. `daily_digest.py` already reads files and needs no LLM
   call; the state file from (1) is all it needs.

**Explicitly NOT recommended:** auto-restarting Gateway from a script. It
holds the broker session; a restart loop fighting a hung process is how you
end up with orders in an unknown state. Alert the human, let the human decide.

---

### R2 — Kronos's top-3 flips on a rank-3/4 tie

**What's actually broken.** Two `--dry-run` calls 30 minutes apart tonight,
identical closed-market data, produced different top-3s:
`[AMZN, MSFT, GOOGL]` then `[AMZN, MSFT, DIS]`. GOOGL and DIS sit ~1 point
apart and swapped ranks 3/4. Six of fourteen tickers moved. The consequence
is not cosmetic: run 1 proposed BUY MSFT + BUY GOOGL (~$50k) and SELL DIS;
run 2 proposed BUY MSFT only and HOLD DIS. **Which trades get placed depends
on which sampling draw you happened to run.**

This compounds the known, documented risk that RiskGuard does *not* cover:
slow bleed from turnover costs on a signal with no measured edge. Noise-driven
rotation is turnover you pay for and get nothing back from.

**Plan — measure before patching. This is a strategy change, so rule 4 applies
in full: it must be backtested honestly, not bolted on because it sounds good.**

1. **Quantify the noise properly** (~1 hour of GPU time, do this first).
   Run K=20 forecasts on identical frozen data, same `sample_count`. For each
   ticker compute mean and standard deviation of predicted return, and the
   empirical distribution of top-3 membership. Output: how big is the sampling
   SD relative to the rank-3/4 gap? Right now we have n=2 and a guess.
   Write it to `research_log/` or `KronosAI/`, not into CLAUDE.md's Empirical
   Findings, until it meets the bar.

2. **Then pick a fix from the measurement, not from intuition.** Candidates,
   roughly in order of preference:
   - **Rotation hysteresis.** Only sell a held name if it falls below rank N
     by a margin; only buy a new name if it beats the incumbent by more than
     ~1 sampling SD. Directly targets noise-driven turnover, cheap to
     implement, easy to reason about.
   - **Raise `sample_count`.** Variance falls as ~1/sqrt(samples), so cutting
     the spread in half costs 4x the inference time (~81s → ~5min per run).
     Acceptable at monthly cadence, probably not hourly.
   - **Average across independent runs.** Statistically the same as raising
     sample_count; only worth it if it parallelises better.

3. **Backtest whichever you choose on the same dates/costs
   `kronos_backtest.py` already uses.** Hysteresis changes the strategy, so
   the existing walk-forward numbers no longer describe it. Report the result
   even if it's worse — especially if it's worse.

4. **Interim operating rule, starting tomorrow, requiring no code:** before
   approving a Kronos rebalance, look at the gap between rank N and N+1. If
   it's ~1 point or less, re-run the signal once and only rotate on names
   that appear in both draws. Costs 90 seconds, avoids paying spread and
   commission on a coin flip.

**Also worth testing while you're in here:** the open hypothesis that Kronos
is an expensive momentum proxy (Spearman 0.916 on one snapshot). The proper
test — rank correlation across the ~24 rebalance dates `kronos_backtest.py`
already covers — is cheap now that the harness exists. If it holds, the
honest conclusion is that we're paying 81s of GPU to reproduce a trailing-
return sort that scored *better* on the hourly IC screen.

---

### R3 — Entries can silently miss, leaving a half-done rebalance

**What's actually broken.** This is the one I'd rank most dangerous, because
it produces a *worse* portfolio than either doing nothing or doing everything.

`execute_rebalance` runs **exits first, then entries** (`paper_trader.py:322`)
— deliberately, to free `max_open_positions` headroom. Entries are DAY limit
orders at `price * 1.005` (`ENTRY_LIMIT_BUFFER`, `paper_trader.py:52`). If the
price runs away, the limit never fills, the order expires at the close, and:

- the exits **did** happen,
- the entries **didn't**,
- you are sitting in unintended cash, concentrated in whatever survived,
- and **nothing tells you**. There's no alert for "the rebalance you approved
  only half-executed."

That is exactly the shape of the 07-27 incident, and the reason it went
unnoticed for a day. The journaling fix from tonight makes each *leg* honest,
but nothing yet checks the *rebalance as a whole*.

**Plan:**

1. **Record intent.** When a rebalance is approved, write the target state
   (symbol → intended qty, and the signal + timestamp that produced it) to
   `rebalance_intent.json`. Small, boring, and the precondition for
   everything else here.

2. **Reconcile at session close.** A launchd job shortly after 16:00 ET
   compares intent against actual IBKR positions. Any divergence — missed
   entry, partial fill, unexpected position — gets journalled as
   `REBALANCE_INCOMPLETE` and texted, naming the specific legs.
   This is the generalisation of tonight's `verify_stop_protection`: same
   idea, one level up.

3. **Decide the policy for a missed entry — this is Koko's call, not the
   code's.** Options: leave it in cash until the next cycle (safest, current
   de-facto behaviour but now visible); retry next session at a fresh limit;
   or widen the buffer. Do **not** auto-widen into a market order — that
   trades a visible miss for invisible slippage.

4. **Consider whether exits-before-entries is still right.** It exists to free
   position headroom, but with `max_open_positions: 8` and 4 held, there's
   currently plenty of room. Interleaving, or entering first when headroom
   allows, would shrink the window where the portfolio is neither the old nor
   the new target. Worth thinking about; not obviously correct; don't change
   it without saying why in the commit.

---

## 3. The reconciliation layer (the real fix)

R1's "was the monitor alive", R3's "did the rebalance complete", tonight's
`verify_stop_protection`, and `reflect_on_trades`' snapshot diff are four
instances of one missing component. Rather than a fifth ad-hoc check, consider
a single `reconcile.py` that answers one question on a schedule:

> **Does IBKR's actual state match what our records say it should be?**

Comparing: positions vs journal, stops vs positions (every position has a live
full-size GTC stop), intent vs outcome for the last rebalance, and
last-successful-contact freshness. One consolidated Telegram on divergence,
silent when clean — the established convention here.

If that had existed, all three of 2026-07-23, 07-25 and 07-27 would have been
caught within 30 minutes instead of taking one, two and one days respectively,
and each needing a human to go looking.

---

## 4. Suggested order

| # | Task | Effort | Why this order |
|---|---|---|---|
| 1 | R1.1 consecutive-failure alerting | ~30 min | Monitor is dead *right now* whenever Gateway is down; everything else assumes it works |
| 2 | R3.1 + R3.2 intent + close reconcile | ~2 h | Highest-consequence silent failure; needed before autotrade |
| 3 | R2.4 interim manual re-run rule | 0 | Free, applies tomorrow, no code |
| 4 | R2.1 measure Kronos sampling noise | ~1 h GPU | Turns a guess into a number |
| 5 | R1.2-1.4, R3.3 policy, `reconcile.py` | ~half day | Consolidation once shapes are known |
| 6 | R2.2/2.3 hysteresis + honest backtest | ~1 day | Strategy change, full rule-4 treatment |

**Do not enable unattended autotrade until at least #1 and #2 are done.** The
whole premise of unattended trading is that failures are noticed without a
human watching, and right now two of the three loudest failure modes are
silent.

---

## 5. Standing reminders for the next session

- Address the owner as **Koko**.
- `./trader_app.sh`, never `python3 trader_app.py` (conda base has no torch).
- Kronos only. Never `allow_momentum` without Koko asking in that session.
- **Verify against IBKR before trusting any document, including this one.**
  This project's records have described a non-existent account twice.
- Read-only checks: `connect(readonly=True)` with a client_id outside
  7 / 9 / 11 / 13 (those are in use by trader_app, paper_trader,
  reflect_on_trades, autotrade_runner).
