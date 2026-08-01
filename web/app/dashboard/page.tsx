"use client";

/**
 * dashboard/page.tsx — the "is everything OK" glance.
 *
 * Two things it refuses to do:
 *
 *  1. Show a green all-clear it hasn't earned. If stop protection is unknown
 *     for any position, the health tile says UNKNOWN — not OK.
 *  2. Present RiskGuard as a safety net that catches losses. The daily-loss
 *     breaker is a PRE-TRADE gate: it is only consulted when an order is
 *     being placed, so it cannot stop a loss arriving from a stop firing on
 *     its own. It didn't fire on the $422 GOOGL loss because nothing tried
 *     to trade that day. The tile says so, because a number labelled
 *     "circuit breaker" invites exactly the wrong assumption.
 */

import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  HelpCircle,
  Power,
} from "lucide-react";

import { api } from "@/lib/api";
import { useFetch, useLive } from "@/lib/use-live";
import {
  DASH,
  fmtPct,
  fmtSigned,
  fmtUsd,
  pnlClass,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { StopBadge } from "@/components/stop-badge";

export default function DashboardPage() {
  const live = useLive();
  const status = useFetch(() => api.status(), [live.revisions.orders]);
  const account = useFetch(() => api.account(), [live.revisions.account]);
  const positions = useFetch(() => api.positions(), [live.revisions.positions]);

  const rows = positions.data?.positions ?? [];
  const unprotected = rows.filter((p) => p.protected === false).length;
  const unknownProtection = rows.filter((p) => p.protected === null).length;

  // "Haven't heard back yet" must never render as "flat". The positions
  // request waits on IBKR and can take seconds; treating a null `data` as an
  // empty portfolio painted a green "No exposure" tile over three live
  // positions the first time this screen ran.
  const positionsKnown = positions.data !== null && !positions.loading;

  const health: "ok" | "warn" | "bad" | "unknown" = !positionsKnown
    ? "unknown"
    : positions.error
      ? "unknown"
      : unprotected > 0
        ? "bad"
        : unknownProtection > 0
          ? "unknown"
          : "ok";

  const totalPnl = rows.reduce((sum, p) => sum + (p.unrealizedPnl ?? 0), 0);
  const limits = status.data?.riskLimits;
  const maxPositions = limits?.max_open_positions ?? null;

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          Account {status.data?.connection.account ?? DASH} ·{" "}
          {status.data?.marketOpen ? "US market open" : "US market closed"}
        </p>
      </div>

      {/* ------------------------------------------------------ stat tiles */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Net liquidation"
          value={fmtUsd(account.data?.netLiquidationUsd, 0)}
          sub={
            account.data?.baseCurrency
              ? `converted from ${account.data.baseCurrency} via IBKR ExchangeRate`
              : undefined
          }
          error={account.data?.conversionError ?? account.error?.message}
        />
        <Tile
          label="Unrealised P&L"
          value={positionsKnown ? fmtSigned(totalPnl) : DASH}
          valueClass={positionsKnown ? pnlClass(totalPnl) : undefined}
          sub={
            positionsKnown
              ? `${rows.length} open position${rows.length === 1 ? "" : "s"}`
              : "waiting on IBKR"
          }
          error={positions.error?.message}
        />
        <Tile
          label="Realised P&L today"
          value={fmtSigned(account.data?.realizedPnl)}
          valueClass={pnlClass(account.data?.realizedPnl)}
          sub="IBKR RealizedPnL for the session"
        />
        <StopHealthTile
          health={health}
          unprotected={unprotected}
          unknown={unknownProtection}
          total={rows.length}
          reason={
            positions.error?.message ??
            positions.data?.openOrdersError ??
            undefined
          }
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* ------------------------------------------------------ positions */}
        <Card className="gap-3 p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-medium">Open positions</h2>
            <Link
              href="/positions"
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              Details <ArrowRight className="size-3" />
            </Link>
          </div>
          {positions.error ? (
            <p className="text-sm text-unknown">
              Unknown — {positions.error.message}
            </p>
          ) : !positionsKnown ? (
            <p className="text-sm text-muted-foreground">
              Asking IBKR…
            </p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              IBKR answered: no open positions.
            </p>
          ) : (
            <div className="space-y-1.5">
              {rows.map((p) => (
                <div
                  key={p.symbol}
                  className="flex items-center justify-between gap-3 rounded-md border border-border/60 px-3 py-2"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium">
                      {p.symbol}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {p.position}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={cn(
                        "text-sm tabular-nums",
                        pnlClass(p.unrealizedPnl)
                      )}
                    >
                      {fmtSigned(p.unrealizedPnl)}{" "}
                      <span className="text-xs opacity-70">
                        {fmtPct(p.unrealizedPct)}
                      </span>
                    </span>
                    <StopBadge position={p} size="sm" />
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* -------------------------------------------------- risk + policy */}
        <Card className="gap-3 p-4">
          <h2 className="font-medium">RiskGuard &amp; policy</h2>

          <dl className="space-y-2 text-sm">
            <Row
              label="Max order notional"
              value={fmtUsd(limits?.max_order_notional_usd, 0)}
            />
            <Row
              label="Max open positions"
              value={
                maxPositions
                  ? `${rows.length} / ${maxPositions}`
                  : DASH
              }
              valueClass={
                maxPositions && rows.length >= maxPositions
                  ? "text-unknown"
                  : undefined
              }
            />
            <Row
              label="Daily-loss breaker"
              value={fmtUsd(limits?.max_daily_loss_usd, 0)}
              hint="Pre-trade gate only — it refuses the NEXT order after a loss. It cannot stop a loss arriving from a stop firing on its own."
            />
            <Row
              label="Stop required on entry"
              value={limits?.require_stop_attached ? "Enforced" : "OFF"}
              valueClass={
                limits?.require_stop_attached ? "text-profit" : "text-loss"
              }
            />
          </dl>

          <div className="mt-1 border-t border-border pt-3 space-y-2 text-sm">
            <Row
              label="Signal"
              value={status.data?.signal.active ?? DASH}
              valueClass="font-mono"
            />
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">Disabled signals</dt>
              <dd className="flex gap-1">
                {(status.data?.signal.disabled ?? []).map((s) => (
                  <Badge
                    key={s}
                    variant="outline"
                    className="gap-1 border-border font-mono text-[10px] text-muted-foreground"
                    title="Gated in signal_policy.py — a caller must pass an explicit opt-in flag, and autotrade refuses to fire rather than substituting another signal."
                  >
                    <Ban className="size-3" />
                    {s}
                  </Badge>
                ))}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">Autotrade</dt>
              <dd>
                {status.data?.autotrade.enabled ? (
                  <Badge variant="destructive" className="gap-1">
                    <Power className="size-3" />
                    ARMED
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-muted-foreground">
                    Off
                  </Badge>
                )}
              </dd>
            </div>
          </div>
        </Card>
      </div>

      {/* -------------------------------------------------------- journal */}
      <Card className="gap-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="font-medium">Journal</h2>
          <Link
            href="/journal"
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            Open <ArrowRight className="size-3" />
          </Link>
        </div>
        <div className="flex flex-wrap gap-4 text-sm">
          <Stat label="Rows" value={String(status.data?.journal.total ?? 0)} />
          <Stat
            label="Blocked"
            value={String(status.data?.journal.blocked ?? 0)}
          />
          <Stat
            label="Corrected"
            value={String(status.data?.journal.superseded ?? 0)}
            hint="Rows a later RESULT_CORRECTED row overturned. Trust the correction, not the original."
          />
          <Stat
            label="Disputed"
            value={String(status.data?.journal.disputed ?? 0)}
            hint="Rows a later NOTE row disowns as phantom or fabricated."
          />
          <Stat
            label="Last entry"
            value={status.data?.journal.lastTimestamp ?? DASH}
          />
        </div>
      </Card>
    </div>
  );
}

function Tile({
  label,
  value,
  sub,
  valueClass,
  error,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
  error?: string | null;
}) {
  return (
    <Card className="gap-1 p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={cn("text-2xl font-semibold tabular-nums", valueClass)}>
        {value}
      </div>
      {error ? (
        <div className="text-xs text-unknown">{error}</div>
      ) : sub ? (
        <div className="text-xs text-muted-foreground">{sub}</div>
      ) : null}
    </Card>
  );
}

function StopHealthTile({
  health,
  unprotected,
  unknown,
  total,
  reason,
}: {
  health: "ok" | "warn" | "bad" | "unknown";
  unprotected: number;
  unknown: number;
  total: number;
  reason?: string;
}) {
  const config = {
    ok: {
      icon: CheckCircle2,
      text: total === 0 ? "No exposure" : "All GTC-covered",
      cls: "text-profit",
    },
    // "warn" is reserved for a future partial state; kept explicit so the
    // union type can't silently fall through to the green case.
    warn: { icon: AlertTriangle, text: "Check stops", cls: "text-unknown" },
    bad: {
      icon: AlertTriangle,
      text: `${unprotected} unprotected`,
      cls: "text-loss",
    },
    unknown: {
      icon: HelpCircle,
      text: unknown > 0 ? `${unknown} unknown` : "Checking…",
      cls: "text-unknown",
    },
  }[health];
  const Icon = config.icon;

  return (
    <Card className="gap-1 p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        Stop protection
      </div>
      <div
        className={cn("flex items-center gap-2 text-xl font-semibold", config.cls)}
      >
        <Icon className="size-5" />
        {config.text}
      </div>
      <div className="text-xs text-muted-foreground line-clamp-2" title={reason}>
        {reason ??
          (total === 0
            ? "Nothing to protect."
            : "Live, GTC, and covering the full quantity.")}
      </div>
    </Card>
  );
}

function Row({
  label,
  value,
  valueClass,
  hint,
}: {
  label: string;
  value: string;
  valueClass?: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground" title={hint}>
        {label}
        {hint && <span className="ml-1 opacity-50">ⓘ</span>}
      </dt>
      <dd className={cn("tabular-nums", valueClass)}>{value}</dd>
    </div>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div title={hint}>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
        {hint && <span className="ml-1 opacity-50">ⓘ</span>}
      </div>
      <div className="font-medium tabular-nums">{value}</div>
    </div>
  );
}
