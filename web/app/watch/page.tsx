"use client";

/**
 * /watch — the station. The screen you open in the morning.
 *
 * Reading order is the argument: what the account is worth NOW, what happened
 * while nobody was awake, how much room is left before a limit kills it, and
 * only then what is actually open. Positions come last on purpose — every FTMO
 * limit is measured on equity INCLUDING floating P&L, so the limits are the
 * thing that can end the account and positions are merely how it happens.
 *
 * Nothing here recomputes a threshold. Every number arrives from
 * `ftmo_rules.evaluate()` over /ws/ftmo or the audit trail. Recomputing a
 * limit in the browser is how the dashboard and the engine end up disagreeing
 * about whether the account is safe.
 *
 * There is no second venue on this page. IBKR was retired on 2026-08-02 and
 * removed entirely on 2026-08-09 — its modules, routes, screens and launchd
 * jobs are gone. Its 46 journal rows remain in the Ledger, labelled `ibkr`,
 * because an audit trail you prune when a venue is retired is not one.
 */

import { useFtmoStream, type FtmoLimit, type FtmoPosition } from "@/lib/use-ftmo";
import { DASH, fmtAge, fmtPrice, fmtSigned, fmtUsd, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { NightBand } from "@/components/night-band";

export default function WatchPage() {
  const { snap, ageMs, live } = useFtmoStream();
  const acct = snap?.account ?? null;
  const v = snap?.verdict ?? null;

  return (
    <div className="mx-auto w-full max-w-[1480px] space-y-5 px-4 py-5">
      <PageHead live={live} ageMs={ageMs} accountId={snap?.connection.account_id ?? null} />

      <section className="grid gap-3 sm:grid-cols-3">
        <Readout
          label="Equity"
          value={acct ? fmtUsd(acct.equity) : DASH}
          note="including floating"
          hint="Every FTMO limit is measured on this, not on balance. The account can fail with no order placed."
          emphasis
        />
        <Readout
          label="Balance"
          value={acct ? fmtUsd(acct.balance) : DASH}
          note="settled"
        />
        <Readout
          label="Floating P&L"
          value={acct ? fmtSigned(acct.floating) : DASH}
          note={acct ? `${snap?.positions.length ?? 0} open` : "—"}
          tone={acct ? pnlClass(acct.floating) : undefined}
        />
      </section>

      {acct && acct.unpricedPositions > 0 && (
        <p className="border hairline border-unknown/45 bg-unknown/10 px-3 py-2 text-xs text-unknown">
          {acct.unpricedPositions} open position
          {acct.unpricedPositions === 1 ? " has" : "s have"} no quote yet, so
          floating P&amp;L excludes {acct.unpricedPositions === 1 ? "it" : "them"}.
          Equity shown is incomplete, not flat.
        </p>
      )}

      <NightBand />

      <section className="space-y-2">
        <h2 className="silkscreen">Limits</h2>
        {v ? (
          <div className="grid gap-3 lg:grid-cols-3">
            <Meter title="Daily loss" limit={v.daily} invert />
            <Meter title="Max drawdown" limit={v.drawdown} invert />
            <Meter
              title="Profit target"
              limit={{
                used: v.profit.usd,
                soft: v.profit.targetUsd,
                flatten: v.profit.targetUsd,
                hard: v.profit.targetUsd,
              }}
            />
          </div>
        ) : (
          <Empty>Waiting for the rule engine.</Empty>
        )}
        {v && (
          <p className="text-xs text-muted-foreground">
            Phase pass: target {v.profit.targetReached ? "met" : "not met"} ·
            {" "}min days {v.profit.minDaysMet ? "met" : "not met"} ·
            {" "}consistency {v.profit.consistencyOk ? "ok" : "breached"}.
            {" "}
            <span className="text-muted-foreground/70">
              Not a trading permission — too few trading days means keep
              trading, not stop.
            </span>
          </p>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-baseline gap-2">
          <h2 className="silkscreen">Open positions</h2>
          <span className="tabular text-xs text-muted-foreground">
            {snap ? snap.positions.length : DASH}
          </span>
        </div>
        {!snap ? (
          <Empty>Connecting to the venue.</Empty>
        ) : snap.positions.length === 0 ? (
          <Empty>Flat. No open positions.</Empty>
        ) : (
          <PositionsTable rows={snap.positions} />
        )}
      </section>
    </div>
  );
}

function PageHead({
  live,
  ageMs,
  accountId,
}: {
  live: boolean;
  ageMs: number | null;
  accountId: number | null;
}) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b hairline border-border pb-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg">FTMO</h1>
        <span className="tabular text-xs text-muted-foreground">
          {accountId ?? DASH}
        </span>
        <span className="silkscreen rounded-sm border hairline border-border px-1.5 py-0.5">
          2-Step · $25,000 · simulated
        </span>
      </div>
      <span className="tabular text-xs text-muted-foreground">
        {live ? "streaming" : "stale"} ·{" "}
        {ageMs === null ? DASH : fmtAge(ageMs / 1000)}
      </span>
    </header>
  );
}

/**
 * A headline number.
 *
 * Renders a dash rather than zero when the venue hasn't answered. `$0.00` and
 * "unknown" look identical at a glance and mean opposite things — this project
 * has already painted a green "no exposure" tile over three live positions.
 */
function Readout({
  label,
  value,
  note,
  hint,
  tone,
  emphasis,
}: {
  label: string;
  value: string;
  note?: string;
  hint?: string;
  tone?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="glass glass-interactive rise px-4 py-3" title={hint}>
      <div className="flex items-baseline justify-between">
        <span className="silkscreen">{label}</span>
        {note && (
          <span className="text-[10px] text-muted-foreground/70">{note}</span>
        )}
      </div>
      <div
        className={cn(
          "tabular mt-1.5",
          emphasis ? "text-[28px] leading-none" : "text-2xl leading-none",
          tone
        )}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * A limit meter with its three thresholds marked.
 *
 * The buffer is the point: FTMO's published number is the one the account must
 * never reach, so soft (stop opening) and flatten (close everything) sit
 * before it. Showing only "used vs hard" would hide the two thresholds that
 * actually govern what the runner does.
 */
function Meter({
  title,
  limit,
  invert = false,
}: {
  title: string;
  limit: FtmoLimit;
  invert?: boolean;
}) {
  const pct = (x: number) =>
    limit.hard > 0 ? Math.max(0, Math.min(100, (x / limit.hard) * 100)) : 0;
  const tone = !invert
    ? "bg-primary"
    : limit.used >= limit.flatten
      ? "bg-loss"
      : limit.used >= limit.soft
        ? "bg-unknown"
        : "bg-primary";

  return (
    <div className="glass glass-interactive rise space-y-2 px-3 py-3">
      <div className="flex items-baseline justify-between">
        <span className="silkscreen">{title}</span>
        <span className="tabular text-sm">
          {fmtUsd(limit.used)}{" "}
          <span className="text-muted-foreground">/ {fmtUsd(limit.hard)}</span>
        </span>
      </div>
      <div className="relative h-1.5 w-full bg-muted">
        <div
          className={cn("absolute inset-y-0 left-0 transition-[width]", tone)}
          style={{ width: `${pct(limit.used)}%` }}
        />
        {invert && (
          <>
            <Tick at={pct(limit.soft)} title="soft — stop opening" />
            <Tick at={pct(limit.flatten)} title="flatten — close everything" />
          </>
        )}
      </div>
      {invert && (
        <div className="tabular flex justify-between text-[10px] text-muted-foreground">
          <span>soft {fmtUsd(limit.soft)}</span>
          <span>flatten {fmtUsd(limit.flatten)}</span>
          <span>hard {fmtUsd(limit.hard)}</span>
        </div>
      )}
    </div>
  );
}

function Tick({ at, title }: { at: number; title: string }) {
  return (
    <span
      title={title}
      className="absolute top-[-2px] h-[10px] w-px bg-foreground/50"
      style={{ left: `${at}%` }}
    />
  );
}

function PositionsTable({ rows }: { rows: FtmoPosition[] }) {
  return (
    <div className="glass overflow-x-auto">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b hairline border-border">
            <Th>Symbol</Th>
            <Th>Side</Th>
            <Th right>Volume</Th>
            <Th right>Entry</Th>
            <Th right>Mark</Th>
            <Th right>Stop</Th>
            <Th right>P&amp;L</Th>
            <Th>Protected</Th>
            <Th right>Quote age</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={p.positionId}
              className="border-b hairline border-border/50 last:border-0"
            >
              <Td>{p.symbol}</Td>
              <Td>
                <span className={p.side === "BUY" ? "text-profit" : "text-loss"}>
                  {p.side}
                </span>
              </Td>
              <Td right mono>{p.volume}</Td>
              <Td right mono>{fmtPrice(p.entryPrice)}</Td>
              {/* A missing mark is DASH, never 0 — an unquoted position is
                  unknown, not worthless. */}
              <Td right mono>{p.mark === null ? DASH : fmtPrice(p.mark)}</Td>
              <Td right mono>
                {p.stopLoss === null ? DASH : fmtPrice(p.stopLoss)}
              </Td>
              <Td right mono className={p.pnl === null ? "" : pnlClass(p.pnl)}>
                {p.pnl === null ? DASH : fmtSigned(p.pnl)}
              </Td>
              <Td>
                {p.protected ? (
                  <span className="text-profit">covered</span>
                ) : (
                  <span className="text-loss">UNPROTECTED</span>
                )}
              </Td>
              <Td right mono>
                {p.quoteAgeS === null ? DASH : fmtAge(p.quoteAgeS)}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={cn("silkscreen px-3 py-2", right ? "text-right" : "text-left")}>
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  mono,
  className = "",
}: {
  children: React.ReactNode;
  right?: boolean;
  mono?: boolean;
  className?: string;
}) {
  return (
    <td
      className={cn("px-3 py-1.5", right && "text-right", mono && "tabular", className)}
    >
      {children}
    </td>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="glass px-3 py-6 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}
