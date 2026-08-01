"use client";

/**
 * positions/page.tsx — open positions and, more importantly, stop health.
 *
 * This is the screen that would have caught both of this project's worst
 * incidents:
 *   - 2026-07-21: bracket stops defaulted to DAY TIF and expired at the
 *     close, leaving three positions unprotected overnight with nobody aware.
 *   - 2026-07-23: GOOGL gapped through its stop and closed, and nothing
 *     recorded it for two days.
 *
 * So the stop column is not a checkmark for "an order exists". It reports
 * the shared `stop_protection_status` verdict — live, GTC, and covering the
 * full quantity — and shows the reason text on every row, including when the
 * answer is "IBKR didn't tell us".
 */

import { useState } from "react";
import { AlertTriangle, ExternalLink, Plus, RefreshCw, Shield, X } from "lucide-react";
import Link from "next/link";

import { api, trade, type TradePreview } from "@/lib/api";
import { useFetch, useLive } from "@/lib/use-live";
import { useShell } from "@/components/app-shell";
import { TradeActionDialog } from "@/components/trade-action";
import { NewTradeDialog } from "@/components/new-trade-dialog";
import {
  DASH,
  fmtPct,
  fmtPrice,
  fmtQty,
  fmtSigned,
  fmtUsd,
  pnlClass,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { StopBadge } from "@/components/stop-badge";

export default function PositionsPage() {
  const live = useLive();
  const { tradingAllowed, gateReason } = useShell();
  const positions = useFetch(() => api.positions(), [live.revisions.positions]);
  const orders = useFetch(
    () => api.orders().catch(() => null),
    [live.revisions.orders]
  );

  // One dialog instance, re-pointed at whichever action was clicked. The
  // loader closure is what decides which preview gets built.
  const [action, setAction] = useState<(() => Promise<TradePreview>) | null>(
    null
  );
  const [dialogOpen, setDialogOpen] = useState(false);
  const [newTradeOpen, setNewTradeOpen] = useState(false);

  const startAction = (loader: () => Promise<TradePreview>) => {
    setAction(() => loader);
    setDialogOpen(true);
  };

  const refreshAll = () => {
    positions.reload();
    orders.reload();
  };

  const rows = positions.data?.positions ?? [];
  // Summary tiles show a dash until IBKR has answered — a zero would read as
  // a fact about the account rather than an artefact of a pending request.
  const known = positions.data !== null && !positions.loading;
  const totalValue = rows.reduce((sum, p) => sum + (p.marketValue ?? 0), 0);
  const totalPnl = rows.reduce((sum, p) => sum + (p.unrealizedPnl ?? 0), 0);
  const unprotected = rows.filter((p) => p.protected === false);
  const unknown = rows.filter((p) => p.protected === null);

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Positions</h1>
          <p className="text-sm text-muted-foreground">
            Live from IBKR — not from the journal.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={refreshAll}
            className="gap-1.5"
          >
            <RefreshCw
              className={cn("size-4", positions.loading && "animate-spin")}
            />
            Refresh
          </Button>
          <Button
            size="sm"
            className="gap-1.5"
            disabled={!tradingAllowed}
            title={tradingAllowed ? "Open a new bracket position" : gateReason}
            onClick={() => setNewTradeOpen(true)}
          >
            <Plus className="size-4" />
            New trade
          </Button>
        </div>
      </div>

      {positions.error && (
        <Card className="border-loss/40 bg-loss/5 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-loss" />
            <div>
              <p className="font-medium text-loss">
                Position state could not be read
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {positions.error.message}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                This does <span className="font-medium">not</span> mean the
                account is flat. Nothing is being shown because nothing is
                known.
              </p>
            </div>
          </div>
        </Card>
      )}

      {unprotected.length > 0 && (
        <Card className="border-loss/50 bg-loss/10 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-loss" />
            <div>
              <p className="font-semibold text-loss">
                {unprotected.length} position
                {unprotected.length === 1 ? "" : "s"} without a covering GTC
                stop
              </p>
              <ul className="mt-1.5 space-y-0.5 text-sm">
                {unprotected.map((p) => (
                  <li key={p.symbol}>
                    <span className="font-mono font-medium">{p.symbol}</span>{" "}
                    <span className="text-muted-foreground">
                      {p.protectionReason}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}

      {unknown.length > 0 && (
        <Card className="border-unknown/40 bg-unknown/10 p-4">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-unknown" />
            <div>
              <p className="font-semibold text-unknown">
                Stop protection is UNKNOWN for {unknown.length} position
                {unknown.length === 1 ? "" : "s"}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {positions.data?.openOrdersError}
              </p>
              <p className="mt-2 text-sm text-muted-foreground">
                Unknown is not the same as unprotected — the stops may well be
                live at IBKR. Retry before acting on this.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* -------------------------------------------------------- summary */}
      <div className="grid gap-3 sm:grid-cols-3">
        <SummaryTile
          label="Open positions"
          value={known ? String(rows.length) : DASH}
        />
        <SummaryTile
          label="Market value"
          value={known ? fmtUsd(totalValue) : DASH}
        />
        <SummaryTile
          label="Unrealised P&L"
          value={known ? fmtSigned(totalPnl) : DASH}
          className={known ? pnlClass(totalPnl) : undefined}
        />
      </div>

      {/* --------------------------------------------------------- table */}
      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-4 py-2.5 text-left font-medium">Symbol</th>
                <th className="px-4 py-2.5 text-right font-medium">Qty</th>
                <th className="px-4 py-2.5 text-right font-medium">Avg cost</th>
                <th className="px-4 py-2.5 text-right font-medium">Last</th>
                <th className="px-4 py-2.5 text-right font-medium">Value</th>
                <th className="px-4 py-2.5 text-right font-medium">
                  Unrealised
                </th>
                <th className="px-4 py-2.5 text-left font-medium">Stop</th>
                <th className="px-4 py-2.5 text-right font-medium" />
              </tr>
            </thead>
            <tbody>
              {/* Only claim "flat" once IBKR has actually answered. A pending
                  request and an empty account look identical in the data and
                  mean opposite things. */}
              {positions.loading && positions.data === null && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-10 text-center text-muted-foreground"
                  >
                    Asking IBKR for positions…
                  </td>
                </tr>
              )}
              {rows.length === 0 && !positions.loading && !positions.error && (
                <tr>
                  <td
                    colSpan={8}
                    className="px-4 py-10 text-center text-muted-foreground"
                  >
                    IBKR answered and reported no open positions. The account
                    is flat.
                  </td>
                </tr>
              )}
              {rows.map((p) => (
                <tr
                  key={p.symbol}
                  className="border-b border-border/60 last:border-0 hover:bg-accent/30"
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-medium">{p.symbol}</span>
                      <Badge
                        variant="outline"
                        className="text-[10px] text-muted-foreground"
                      >
                        {p.secType}
                      </Badge>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {fmtQty(p.position)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                    {fmtPrice(p.avgCost)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {fmtPrice(p.marketPrice)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                    {fmtUsd(p.marketValue, 0)}
                  </td>
                  <td
                    className={cn(
                      "px-4 py-3 text-right tabular-nums",
                      pnlClass(p.unrealizedPnl)
                    )}
                  >
                    <div>{fmtSigned(p.unrealizedPnl)}</div>
                    <div className="text-xs opacity-80">
                      {fmtPct(p.unrealizedPct)}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col items-start gap-1">
                      <StopBadge position={p} />
                      {p.stops.map((s, i) => (
                        <span
                          key={i}
                          className="font-mono text-[11px] text-muted-foreground"
                        >
                          {fmtQty(s.qty)} @ {fmtPrice(s.price)} · {s.tif} ·{" "}
                          {s.status}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 gap-1 px-2 text-xs"
                        disabled={!tradingAllowed}
                        title={
                          tradingAllowed
                            ? "Place a fresh full-size GTC stop"
                            : gateReason
                        }
                        onClick={() => {
                          // Default to the project's own stop distance:
                          // 2xATR below entry is what paper_trader sizes to.
                          // Falls back to 5% if no market price is known.
                          const ref = p.marketPrice ?? p.avgCost;
                          const suggested = Number(
                            (p.position > 0 ? ref * 0.95 : ref * 1.05).toFixed(2)
                          );
                          const entered = window.prompt(
                            `GTC stop price for ${p.symbol} (${p.position > 0 ? "long" : "short"} ${Math.abs(p.position)} @ ${fmtPrice(p.avgCost)}, last ${fmtPrice(ref)})`,
                            String(suggested)
                          );
                          if (!entered) return;
                          const stop = Number(entered);
                          if (!Number.isFinite(stop) || stop <= 0) return;
                          startAction(() =>
                            trade.previewReprotect(p.symbol, stop)
                          );
                        }}
                      >
                        <Shield className="size-3" />
                        Stop
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 gap-1 px-2 text-xs text-loss hover:text-loss"
                        disabled={!tradingAllowed}
                        title={tradingAllowed ? "Close this position" : gateReason}
                        onClick={() =>
                          startAction(() => trade.previewFlatten(p.symbol))
                        }
                      >
                        Flatten
                      </Button>
                      <Link
                        href="/charts"
                        className="inline-flex items-center gap-1 px-1 text-xs text-muted-foreground hover:text-foreground"
                        title="Open in charts"
                      >
                        <ExternalLink className="size-3" />
                      </Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* --------------------------------------------------- open orders */}
      <div>
        <h2 className="mb-2 text-lg font-medium">Open orders</h2>
        {orders.data === null || orders.data === undefined ? (
          <Card className="border-unknown/40 bg-unknown/5 p-4 text-sm text-muted-foreground">
            Open-order state is unavailable — IBKR did not answer. An empty
            list is not being shown here, because that would be a claim this
            request never established.
          </Card>
        ) : orders.data.orders.length === 0 ? (
          <Card className="p-4 text-sm text-muted-foreground">
            IBKR answered: no open orders.
          </Card>
        ) : (
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-4 py-2.5 text-left font-medium">
                      Symbol
                    </th>
                    <th className="px-4 py-2.5 text-left font-medium">Side</th>
                    <th className="px-4 py-2.5 text-left font-medium">Type</th>
                    <th className="px-4 py-2.5 text-right font-medium">Qty</th>
                    <th className="px-4 py-2.5 text-right font-medium">
                      Limit
                    </th>
                    <th className="px-4 py-2.5 text-right font-medium">Stop</th>
                    <th className="px-4 py-2.5 text-left font-medium">TIF</th>
                    <th className="px-4 py-2.5 text-left font-medium">
                      Status
                    </th>
                    <th className="px-4 py-2.5 text-right font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {orders.data.orders.map((o) => (
                    <tr
                      key={o.orderId}
                      className="border-b border-border/60 last:border-0"
                    >
                      <td className="px-4 py-2.5 font-mono">{o.symbol}</td>
                      <td
                        className={cn(
                          "px-4 py-2.5",
                          o.action === "BUY" ? "text-profit" : "text-loss"
                        )}
                      >
                        {o.action}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        {o.orderType}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {fmtQty(o.quantity)}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums text-muted-foreground">
                        {o.limitPrice ? fmtPrice(o.limitPrice) : DASH}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">
                        {o.stopPrice ? fmtPrice(o.stopPrice) : DASH}
                      </td>
                      <td className="px-4 py-2.5">
                        {/* TIF is called out because a DAY stop is the bug
                            that left three positions naked overnight. */}
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-[10px]",
                            o.orderType.startsWith("STP") && o.tif !== "GTC"
                              ? "border-loss/50 text-loss"
                              : "text-muted-foreground"
                          )}
                        >
                          {o.tif}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        {o.status}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1 px-2 text-xs"
                          disabled={!tradingAllowed}
                          title={tradingAllowed ? "Cancel this order" : gateReason}
                          onClick={() =>
                            startAction(() => trade.previewCancel(o.orderId))
                          }
                        >
                          <X className="size-3" />
                          Cancel
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </div>

      <TradeActionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        loadPreview={action}
        onDone={refreshAll}
      />
      <NewTradeDialog
        open={newTradeOpen}
        onOpenChange={setNewTradeOpen}
        onDone={refreshAll}
      />
    </div>
  );
}

function SummaryTile({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <Card className="gap-1 p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={cn("text-2xl font-semibold tabular-nums", className)}>
        {value}
      </div>
    </Card>
  );
}
