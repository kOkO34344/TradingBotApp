"use client";

/**
 * dashboard/page.tsx — the FTMO account at a glance.
 *
 * MOVED OFF IBKR 2026-08-07. This screen used to read `/api/status`,
 * `/api/account` and `/api/positions`, all of which are IB Gateway — retired
 * by rule 9 and no longer dialled — so every tile was a connection error.
 *
 * What it shows now is the thing that can actually end the account: **how much
 * headroom is left against each FTMO limit.** That is a different question
 * from "how am I doing", and it is the one worth putting on a dashboard,
 * because these limits are measured on EQUITY INCLUDING FLOATING P&L. The
 * account can breach with no order placed, which is precisely why this venue
 * gets a continuous monitor rather than a pre-trade gate like RiskGuard —
 * a pre-trade gate structurally cannot see a loss that arrives on its own.
 *
 * Each limit has three thresholds and they are NOT interchangeable:
 *   soft    — stop opening new exposure
 *   flatten — close everything
 *   hard    — the account is gone
 * Collapsing them into one bar would hide which one you are about to cross.
 *
 * Rule 1 throughout: a limit whose frame has not arrived renders as —, never
 * as a comfortable zero. A green "0% used" tile over an unknown account is the
 * same failure class as the phantom liquidation this project already had.
 */

import Link from "next/link";
import { AlertTriangle, ArrowRight, ShieldAlert } from "lucide-react";

import { useFtmoStream, type FtmoLimit } from "@/lib/use-ftmo";
import { DASH, fmtSigned, fmtUsd, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

export function DashboardScreen() {
  const ftmo = useFtmoStream();
  const snap = ftmo.snap;
  const account = snap?.account ?? null;
  const verdict = snap?.verdict ?? null;
  const positions = snap?.positions ?? [];

  const unprotected = positions.filter((p) => !p.protected);

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            FTMO Challenge account — headroom against every limit that can end
            it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!ftmo.live && (
            <Badge
              variant="outline"
              className="border-unknown/50 text-unknown gap-1"
            >
              <AlertTriangle className="size-3" />
              {snap ? "STALE FRAME" : "CONNECTING"}
            </Badge>
          )}
          <PostureBadge posture={verdict?.posture ?? null} />
        </div>
      </div>

      {/* The engine's own verdict, in its own words. Never paraphrased here:
          `ftmo_rules` decides, and a dashboard that restates its reasoning can
          drift from it. */}
      {verdict && (verdict.mustFlatten || !verdict.canOpen) && (
        <Card
          className={cn(
            "p-3.5",
            verdict.mustFlatten
              ? "border-loss/40 bg-loss/5"
              : "border-unknown/40 bg-unknown/5"
          )}
        >
          <div className="flex items-start gap-2.5">
            <ShieldAlert
              className={cn(
                "mt-0.5 size-5 shrink-0",
                verdict.mustFlatten ? "text-loss" : "text-unknown"
              )}
            />
            <div className="space-y-1 text-sm">
              <p
                className={cn(
                  "font-medium",
                  verdict.mustFlatten ? "text-loss" : "text-unknown"
                )}
              >
                {verdict.mustFlatten
                  ? "Rule engine says FLATTEN"
                  : "New entries are blocked"}
              </p>
              <p className="text-muted-foreground">
                {verdict.reasons.join("; ")}
              </p>
            </div>
          </div>
        </Card>
      )}

      {unprotected.length > 0 && (
        <Card className="border-loss/40 bg-loss/5 p-3.5">
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 size-5 shrink-0 text-loss" />
            <p className="text-sm">
              <span className="font-medium text-loss">
                {unprotected.length} position
                {unprotected.length === 1 ? "" : "s"} without a stop
              </span>{" "}
              <span className="text-muted-foreground">
                ({unprotected.map((p) => p.symbol).join(", ")}) —{" "}
                <Link href="/watch" className="underline hover:text-foreground">
                  open positions
                </Link>
              </span>
            </p>
          </div>
        </Card>
      )}

      {/* ------------------------------------------------------------ money */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Equity"
          value={account ? fmtUsd(account.equity) : DASH}
          hint="Balance plus floating P&L. Every limit below is measured against THIS, not balance."
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
              ? `${account.unpricedPositions} could not be priced — new entries are blocked while true`
              : undefined
          }
        />
      </div>

      {/* ----------------------------------------------------------- limits */}
      <div className="grid gap-3 lg:grid-cols-2">
        <LimitCard
          title="Daily loss"
          limit={verdict?.daily ?? null}
          note="Measured from the balance at 00:00 Prague time, which is why the runner keeps its own state file — a one-shot script cannot remember the day-start balance, and a daily loss of 0.00 forever is a limit that can never trip."
        />
        <LimitCard
          title="Max drawdown"
          limit={verdict?.drawdown ?? null}
          note={
            verdict?.drawdown.floorEquity
              ? `Equity floor ${fmtUsd(verdict.drawdown.floorEquity)}. On the 1-Step product this floor trails a completed day's closing balance.`
              : "The trailing floor moves off a completed day's closing balance."
          }
        />
      </div>

      {/* ----------------------------------------------------------- target */}
      <Card className="p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-medium">Profit target</h2>
          <span className="text-sm tabular-nums">
            {verdict ? (
              <>
                <span className={pnlClass(verdict.profit.usd)}>
                  {fmtSigned(verdict.profit.usd)}
                </span>
                <span className="text-muted-foreground">
                  {" "}
                  / {fmtUsd(verdict.profit.targetUsd)}
                </span>
              </>
            ) : (
              DASH
            )}
          </span>
        </div>
        <Meter
          used={verdict ? Math.max(0, verdict.profit.usd) : null}
          of={verdict?.profit.targetUsd ?? null}
          tone="profit"
        />
        {/* Hitting the number is not the same as passing, and showing only the
            number would imply it is. */}
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          <Condition met={verdict?.profit.targetReached ?? null} label="Target reached" />
          <Condition met={verdict?.profit.minDaysMet ?? null} label="Minimum days" />
          <Condition met={verdict?.profit.consistencyOk ?? null} label="Consistency" />
          <Condition met={verdict?.profit.canPass ?? null} label="Can pass" strong />
        </div>
      </Card>

      <p className="text-xs text-muted-foreground">
        IBKR is retired (rule 9) and is not shown here. Its three open positions
        are still managed by <code>reflect_on_trades.py</code> on its own
        schedule. The venue that trades is{" "}
        <Link href="/watch" className="underline hover:text-foreground">
          FTMO
        </Link>
        .
      </p>
    </div>
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

function PostureBadge({ posture }: { posture: string | null }) {
  if (!posture) {
    return (
      <Badge variant="outline" className="text-muted-foreground">
        {DASH}
      </Badge>
    );
  }
  const tone =
    posture === "OK"
      ? "border-profit/40 text-profit"
      : posture === "BLOCKED"
        ? "border-unknown/50 text-unknown"
        : "border-loss/50 text-loss";
  return (
    <Badge variant="outline" className={cn("font-medium", tone)}>
      {posture}
    </Badge>
  );
}

/**
 * One limit, with all three of its thresholds visible.
 *
 * The bar is scaled to the HARD threshold, so the soft and flatten marks sit
 * where they actually are. Scaling to the soft limit would make a breach of it
 * look like the end of the scale, which is the opposite of the truth — there
 * is real distance between "stop opening" and "the account is gone", and that
 * distance is the information.
 */
function LimitCard({
  title,
  limit,
  note,
}: {
  title: string;
  limit: FtmoLimit | null;
  note: string;
}) {
  const pct = limit && limit.hard > 0 ? (limit.used / limit.hard) * 100 : null;
  const tone =
    limit === null
      ? "unknown"
      : limit.used >= limit.flatten
        ? "loss"
        : limit.used >= limit.soft
          ? "unknown"
          : "profit";

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-medium">{title}</h2>
        <span className="text-sm tabular-nums">
          {limit ? (
            <>
              <span
                className={cn(
                  tone === "loss" && "text-loss",
                  tone === "unknown" && "text-unknown"
                )}
              >
                {fmtUsd(limit.used)}
              </span>
              <span className="text-muted-foreground">
                {" "}
                used of {fmtUsd(limit.hard)}
              </span>
            </>
          ) : (
            DASH
          )}
        </span>
      </div>

      <Meter used={limit?.used ?? null} of={limit?.hard ?? null} tone={tone} />

      {limit && (
        <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
          <Threshold label="Soft" value={limit.soft} hint="Stop opening new exposure" />
          <Threshold label="Flatten" value={limit.flatten} hint="Close everything" />
          <Threshold label="Hard" value={limit.hard} hint="Account failed" />
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground/80">
        {note}
      </p>
      {pct !== null && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {pct.toFixed(1)}% of the hard limit consumed.
        </p>
      )}
    </Card>
  );
}

function Threshold({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint: string;
}) {
  return (
    <div title={hint}>
      <p className="uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="tabular-nums">{fmtUsd(value)}</p>
    </div>
  );
}

function Meter({
  used,
  of,
  tone,
}: {
  used: number | null;
  of: number | null;
  tone: "profit" | "loss" | "unknown";
}) {
  // Unknown draws an empty track, not a full green bar.
  const pct =
    used === null || of === null || of <= 0
      ? null
      : Math.min(100, Math.max(0, (used / of) * 100));
  return (
    <div className="mt-2.5 h-2 w-full overflow-hidden rounded-full bg-muted">
      {pct !== null && (
        <div
          className={cn(
            "h-full rounded-full transition-all",
            tone === "loss"
              ? "bg-loss"
              : tone === "unknown"
                ? "bg-unknown"
                : "bg-profit"
          )}
          style={{ width: `${pct}%` }}
        />
      )}
    </div>
  );
}

function Condition({
  met,
  label,
  strong,
}: {
  met: boolean | null;
  label: string;
  strong?: boolean;
}) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1",
        met === null
          ? "text-muted-foreground"
          : met
            ? "border-profit/40 text-profit"
            : "border-muted-foreground/30 text-muted-foreground",
        strong && met && "font-semibold"
      )}
    >
      {met === null ? DASH : met ? "✓" : "○"} {label}
    </Badge>
  );
}

export function DashboardLink() {
  return (
    <Link href="/watch" className="inline-flex items-center gap-1 underline">
      FTMO <ArrowRight className="size-3" />
    </Link>
  );
}
