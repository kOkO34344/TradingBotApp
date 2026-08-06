"use client";

import { useFtmoStream, type FtmoLimit, type FtmoPosition } from "@/lib/use-ftmo";
import { DASH, fmtAge, fmtPrice, fmtSigned, fmtUsd, pnlClass } from "@/lib/format";
import { FtmoKronosPanel } from "@/components/ftmo-kronos-panel";

/**
 * FTMO venue dashboard.
 *
 * Every limit on this venue is measured on equity INCLUDING floating P&L, so
 * the account can fail with no order placed. That is why this screen leads
 * with the three limit meters rather than with positions: the meters are the
 * thing that can kill the account, and positions are only how it happens.
 *
 * Nothing here recomputes a threshold. Every number arrives from
 * `ftmo_rules.evaluate()` via /ws/ftmo. Recomputing a limit in the browser is
 * how the dashboard and the engine end up disagreeing about whether the
 * account is safe.
 */
export default function FtmoPage() {
  const { snap, ageMs, live } = useFtmoStream();
  const conn = snap?.connection;
  const acct = snap?.account ?? null;
  const v = snap?.verdict ?? null;

  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-5 space-y-5">
      <Header live={live} ageMs={ageMs} status={conn?.status} error={conn?.error}
              accountId={conn?.account_id ?? null} />

      <section className="grid gap-px bg-border sm:grid-cols-3 border hairline border-border">
        <Stat label="Balance" value={acct ? fmtUsd(acct.balance) : DASH} />
        <Stat label="Equity (incl. floating)"
              value={acct ? fmtUsd(acct.equity) : DASH}
              hint="Every FTMO limit is measured on this, not on balance." />
        <Stat label="Floating P&L"
              value={acct ? fmtSigned(acct.floating) : DASH}
              tone={acct ? pnlClass(acct.floating) : undefined} />
      </section>

      {acct && acct.unpricedPositions > 0 && (
        <p className="border hairline border-loss/40 bg-loss/5 px-3 py-2 text-xs text-loss">
          {acct.unpricedPositions} open position
          {acct.unpricedPositions === 1 ? " has" : "s have"} no quote yet, so
          floating P&amp;L excludes {acct.unpricedPositions === 1 ? "it" : "them"}.
          Equity shown is incomplete, not flat.
        </p>
      )}

      <section className="space-y-3">
        <SectionTitle>Limits</SectionTitle>
        {v ? (
          <div className="grid gap-4 lg:grid-cols-3">
            <Meter title="Daily loss" limit={v.daily} invert />
            <Meter title="Max drawdown" limit={v.drawdown} invert />
            <Meter
              title="Profit target"
              limit={{ used: v.profit.usd, soft: v.profit.targetUsd,
                       flatten: v.profit.targetUsd, hard: v.profit.targetUsd }}
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

      <FtmoKronosPanel />

      <section className="space-y-3">
        <SectionTitle>
          Positions{" "}
          <span className="text-muted-foreground">
            {snap ? snap.positions.length : DASH}
          </span>
        </SectionTitle>
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

function Header({ live, ageMs, status, error, accountId }: {
  live: boolean; ageMs: number | null; status?: string;
  error?: string | null; accountId: number | null;
}) {
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-3 border-b hairline border-border pb-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg tracking-tight">FTMO</h1>
        <span className="tabular text-xs text-muted-foreground">
          {accountId ?? DASH}
        </span>
        <span className="border hairline border-border px-1.5 py-0.5 text-[10px] uppercase tracking-widest text-muted-foreground">
          2-Step · $25,000 · simulated
        </span>
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span
          className={`inline-block h-1.5 w-1.5 rounded-full ${
            live ? "bg-profit" : "bg-loss"
          }`}
        />
        <span className={live ? "text-muted-foreground" : "text-loss"}>
          {live ? "streaming" : status === "error" ? "disconnected" : "stale"}
        </span>
        <span className="tabular text-muted-foreground/70">
          {ageMs === null ? DASH : fmtAge(ageMs / 1000)}
        </span>
        {error && <span className="text-loss">· {error}</span>}
      </div>
    </header>
  );
}

/**
 * A limit meter with its three thresholds marked.
 *
 * The buffer is the point: FTMO's published number is the one we must never
 * reach, so soft (stop opening) and flatten (close everything) sit before it.
 * Showing only "used vs hard" would hide the two thresholds that actually
 * govern behaviour.
 */
function Meter({ title, limit, invert = false }: {
  title: string; limit: FtmoLimit; invert?: boolean;
}) {
  const pct = (x: number) => Math.max(0, Math.min(100, (x / limit.hard) * 100));
  const usedPct = pct(limit.used);
  const tone =
    !invert ? "bg-primary"
      : limit.used >= limit.flatten ? "bg-loss"
      : limit.used >= limit.soft ? "bg-unknown"
      : "bg-primary";

  return (
    <div className="border hairline border-border p-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-widest text-muted-foreground">
          {title}
        </span>
        <span className="tabular text-sm">
          {fmtUsd(limit.used)}{" "}
          <span className="text-muted-foreground">/ {fmtUsd(limit.hard)}</span>
        </span>
      </div>
      <div className="relative h-1.5 w-full bg-secondary">
        <div className={`absolute inset-y-0 left-0 ${tone} transition-[width] duration-200`}
             style={{ width: `${usedPct}%` }} />
        {invert && (
          <>
            <Tick at={pct(limit.soft)} title="soft — stop opening" />
            <Tick at={pct(limit.flatten)} title="flatten — close everything" />
          </>
        )}
      </div>
      {invert && (
        <div className="flex justify-between tabular text-[10px] text-muted-foreground">
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
    <span title={title}
          className="absolute top-[-2px] h-[10px] w-px bg-foreground/50"
          style={{ left: `${at}%` }} />
  );
}

function PositionsTable({ rows }: { rows: FtmoPosition[] }) {
  return (
    <div className="overflow-x-auto border hairline border-border">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b hairline border-border text-[10px] uppercase tracking-widest text-muted-foreground">
            <Th>Symbol</Th><Th>Side</Th><Th right>Volume</Th><Th right>Entry</Th>
            <Th right>Mark</Th><Th right>Stop</Th><Th right>P&amp;L</Th>
            <Th>Protected</Th><Th right>Quote age</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.positionId}
                className="border-b hairline border-border/50 last:border-0">
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
    <th className={`px-3 py-2 font-medium ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

function Td({ children, right, mono, className = "" }: {
  children: React.ReactNode; right?: boolean; mono?: boolean; className?: string;
}) {
  return (
    <td className={`px-3 py-1.5 ${right ? "text-right" : ""} ${
      mono ? "tabular" : ""} ${className}`}>
      {children}
    </td>
  );
}

function Stat({ label, value, hint, tone }: {
  label: string; value: string; hint?: string; tone?: string;
}) {
  return (
    <div className="bg-background p-3" title={hint}>
      <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      <div className={`tabular mt-1 text-2xl ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[10px] uppercase tracking-widest text-muted-foreground">
      {children}
    </h2>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <p className="border hairline border-border px-3 py-6 text-center text-sm text-muted-foreground">
      {children}
    </p>
  );
}
