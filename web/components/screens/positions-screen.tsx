"use client";

/**
 * positions/page.tsx — open FTMO positions, and above all their stop health.
 *
 * MOVED OFF IBKR 2026-08-07. This screen used to read `/api/positions`, which
 * is IB Gateway, which rule 9 retired and which the backend no longer dials —
 * so it showed nothing but a connection error. It now reads the same
 * `/ws/ftmo` frame the FTMO screen uses, so the two cannot disagree about what
 * is held.
 *
 * The stop column is the reason this screen exists. It would have caught both
 * of this project's worst incidents — the 2026-07-21 bracket stops that
 * defaulted to DAY TIF and expired at the close leaving three positions
 * unprotected overnight, and the 2026-07-23 GOOGL close that nothing recorded
 * for two days.
 *
 * FTMO changes the SHAPE of that question, and this screen states the new
 * shape rather than pretending it is the old one. An FTMO stop is a field on
 * the position itself, returned in the same frame as the position, so it
 * cannot expire the way a DAY order can and there is no separate order to go
 * missing. Two states, not three: protected, or not. "Unknown" is a state
 * IBKR could be in because `reqAllOpenOrders` could wedge; asking this venue
 * for a position and getting one IS the answer.
 *
 * What has NOT changed is that an unprotected position is reported loudly,
 * because on this venue a limit is measured on equity including floating P&L
 * — the account can fail with no order placed.
 */

import { AlertTriangle, RefreshCw, Shield, ShieldAlert } from "lucide-react";
import Link from "next/link";

import { useFtmoStream, type FtmoPosition } from "@/lib/use-ftmo";
import { DASH, fmtPrice, fmtSigned, fmtUsd, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

export function PositionsScreen() {
  const ftmo = useFtmoStream();
  const snap = ftmo.snap;
  const positions = snap?.positions ?? [];
  const account = snap?.account ?? null;

  const unprotected = positions.filter((p) => !p.protected);
  const totalPnl = positions.reduce(
    (sum, p) => (p.pnl === null ? sum : sum + p.pnl),
    0
  );
  // A position we could not price is not one we can total honestly. Rule 1:
  // an unpriced leg makes the sum unknown, not zero.
  const anyUnpriced = positions.some((p) => p.pnl === null);

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Positions</h1>
          <p className="text-sm text-muted-foreground">
            Open positions on the FTMO Challenge account, with the stop each one
            carries at the venue.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!ftmo.live && (
            <Badge
              variant="outline"
              className="border-unknown/50 text-unknown gap-1"
              title={
                snap
                  ? "The venue socket has gone quiet. Showing the last frame received."
                  : "No frame has arrived yet."
              }
            >
              <RefreshCw className="size-3" />
              {snap ? "STALE" : "CONNECTING"}
            </Badge>
          )}
          <Badge variant="outline" className="font-mono text-[11px]">
            FTMO {account?.accountId ?? DASH}
          </Badge>
        </div>
      </div>

      {/* An unprotected position is the single thing this screen exists to
          shout about, so it goes above everything else. */}
      {unprotected.length > 0 && (
        <Card className="border-loss/40 bg-loss/5 p-3.5">
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 size-5 shrink-0 text-loss" />
            <div className="space-y-1 text-sm">
              <p className="font-medium text-loss">
                {unprotected.length} position
                {unprotected.length === 1 ? "" : "s"} without a stop
              </p>
              <p className="text-muted-foreground">
                {unprotected.map((p) => p.symbol).join(", ")} — every FTMO limit
                is measured on equity including floating P&amp;L, so an
                unprotected position can fail the account with no order placed.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* ------------------------------------------------------------ tiles */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Equity"
          value={account ? fmtUsd(account.equity) : DASH}
          hint="Balance plus floating P&L — the number every FTMO limit is measured against."
        />
        <Tile
          label="Balance"
          value={account ? fmtUsd(account.balance) : DASH}
          hint="Settled cash, before open positions are marked."
        />
        <Tile
          label="Floating P&L"
          value={account ? fmtSigned(account.floating) : DASH}
          className={account ? pnlClass(account.floating) : undefined}
          hint="Marked at the side each position would close on, never at the mid."
        />
        <Tile
          label="Open positions"
          value={snap ? String(positions.length) : DASH}
          hint={
            account?.unpricedPositions
              ? `${account.unpricedPositions} could not be priced`
              : undefined
          }
        />
      </div>

      {/* ------------------------------------------------------------ table */}
      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2.5 text-left font-medium">Symbol</th>
                <th className="px-3 py-2.5 text-left font-medium">Side</th>
                <th className="px-3 py-2.5 text-right font-medium">Units</th>
                <th className="px-3 py-2.5 text-right font-medium">Entry</th>
                <th className="px-3 py-2.5 text-right font-medium">Mark</th>
                <th className="px-3 py-2.5 text-right font-medium">P&amp;L</th>
                <th className="px-3 py-2.5 text-left font-medium">Stop</th>
                <th className="px-3 py-2.5 text-right font-medium">Quote age</th>
              </tr>
            </thead>
            <tbody>
              {!snap && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-3 py-10 text-center text-muted-foreground"
                  >
                    Waiting for the first frame from the venue…
                  </td>
                </tr>
              )}
              {snap && positions.length === 0 && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-3 py-10 text-center text-muted-foreground"
                  >
                    No open positions on FTMO.
                  </td>
                </tr>
              )}
              {positions.map((p) => (
                <PositionRow key={p.positionId} p={p} />
              ))}
            </tbody>
            {positions.length > 0 && (
              <tfoot>
                <tr className="border-t border-border">
                  <td
                    colSpan={5}
                    className="px-3 py-2.5 text-right text-xs uppercase tracking-wide text-muted-foreground"
                  >
                    Total
                  </td>
                  <td
                    className={cn(
                      "px-3 py-2.5 text-right font-medium tabular-nums",
                      anyUnpriced ? "text-unknown" : pnlClass(totalPnl)
                    )}
                    title={
                      anyUnpriced
                        ? "At least one position has no quote, so this total is incomplete."
                        : undefined
                    }
                  >
                    {anyUnpriced ? DASH : fmtSigned(totalPnl)}
                  </td>
                  <td colSpan={2} />
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </Card>

      <p className="text-xs text-muted-foreground">
        IBKR still holds three positions from before rule 9 retired it. They are
        not shown here and are not managed from the browser —{" "}
        <code>reflect_on_trades.py</code> monitors them on its own schedule. Set{" "}
        <code>ibkr.web_enabled</code> true in <code>trader_settings.json</code>{" "}
        to bring those screens back. The live venue is{" "}
        <Link href="/watch" className="underline hover:text-foreground">
          FTMO
        </Link>
        .
      </p>
    </div>
  );
}

function PositionRow({ p }: { p: FtmoPosition }) {
  return (
    <tr className="border-b border-border/50 last:border-0 hover:bg-accent/30">
      <td className="px-3 py-2 font-mono font-medium">
        <Link href="/market" className="hover:underline" title="Open on the chart">
          {p.symbol}
        </Link>
      </td>
      <td
        className={cn(
          "px-3 py-2 font-medium",
          p.side === "BUY" ? "text-profit" : "text-loss"
        )}
      >
        {p.side}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {p.units.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {fmtPrice(p.entryPrice)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {/* No quote is not a price of zero. */}
        {p.mark === null ? (
          <span className="text-unknown" title="No quote — this position cannot be marked">
            {DASH}
          </span>
        ) : (
          fmtPrice(p.mark)
        )}
      </td>
      <td className={cn("px-3 py-2 text-right tabular-nums", pnlClass(p.pnl))}>
        {p.pnl === null ? DASH : fmtSigned(p.pnl)}
      </td>
      <td className="px-3 py-2">
        {p.protected && p.stopLoss ? (
          <Badge
            variant="outline"
            className="border-profit/40 text-profit gap-1 font-mono"
            title="Server-side stop, attached to the position at the venue. It cannot expire the way an IBKR DAY order can."
          >
            <Shield className="size-3" />
            {fmtPrice(p.stopLoss)}
          </Badge>
        ) : (
          <Badge variant="destructive" className="gap-1">
            <AlertTriangle className="size-3" />
            UNPROTECTED
          </Badge>
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
        {p.quoteAgeS === null ? DASH : `${p.quoteAgeS.toFixed(1)}s`}
      </td>
    </tr>
  );
}

function Tile({
  label,
  value,
  hint,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  className?: string;
}) {
  return (
    <Card className="p-3.5" title={hint}>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className={cn("mt-1 text-xl font-semibold tabular-nums", className)}>
        {value}
      </p>
      {hint && (
        <p className="mt-1 line-clamp-2 text-[11px] text-muted-foreground/80">
          {hint}
        </p>
      )}
    </Card>
  );
}
