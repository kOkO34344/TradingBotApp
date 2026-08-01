"use client";

/**
 * rebalance/page.tsx — the approve screen that replaces the terminal y/N.
 *
 * It replaces the prompt and nothing else. The run is a single
 * `execute_rebalance` call that pauses at the approval point (see
 * api/rebalance.py), so the proposal on this screen and the orders that go
 * out afterwards come from the same computed plan. Approving here is exactly
 * as binding as typing `y` was.
 *
 * The screen shows the full ranking, not just the winners, because the
 * decision that matters is at the top-N boundary — and with this signal the
 * boundary is often inside the sampling noise.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Loader2,
  Play,
  RefreshCw,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  ApiError,
  jobs as jobsApi,
  rebalance,
  type Job,
  type RebalanceProposal,
} from "@/lib/api";
import { fmtPct, fmtPrice, fmtQty, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useShell } from "@/components/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

export default function RebalancePage() {
  const { tradingAllowed, gateReason } = useShell();
  const [job, setJob] = useState<Job<unknown> | null>(null);
  const [proposal, setProposal] = useState<RebalanceProposal | null>(null);
  const [deciding, setDeciding] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const { pending, job: running } = await rebalance.pending();
      setProposal(pending);
      if (running) setJob(running);
      else if (pending === null) {
        setJob((prev) =>
          prev && prev.status === "running" ? prev : prev
        );
      }
    } catch {
      /* transient */
    }
  }, []);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, 2000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [refresh]);

  // Keep the job record current so the log and final status show up.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const t = setInterval(async () => {
      try {
        setJob(await jobsApi.get(job.id));
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [job]);

  const start = async (dryRun: boolean) => {
    try {
      const started = await rebalance.start(dryRun);
      setJob(started);
      setProposal(null);
      toast.info(dryRun ? "Dry run started" : "Rebalance started", {
        description:
          "Computing the signal. This takes a couple of minutes — the proposal appears here when it's ready.",
      });
    } catch (err) {
      toast.error("Could not start", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    }
  };

  const decide = async (approved: boolean) => {
    if (!proposal) return;
    setDeciding(true);
    try {
      await rebalance.decide(proposal.jobId, approved);
      toast[approved ? "warning" : "success"](
        approved ? "Approved — placing orders" : "Declined",
        {
          description: approved
            ? "Exits first, then entries, all through RiskGuard. Watch the log below."
            : "Nothing was placed. The declined proposal is journalled.",
        }
      );
      setProposal(null);
      refresh();
    } catch (err) {
      toast.error("Decision failed", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setDeciding(false);
    }
  };

  const busy = job?.status === "running" || job?.status === "queued";

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rebalance</h1>
          <p className="text-sm text-muted-foreground">
            Runs the signal, proposes the diff, and waits for your decision —
            the same code path as <code>paper_trader.py</code>, with this
            screen instead of the y/N prompt.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => start(true)}
            disabled={busy}
            className="gap-1.5"
          >
            <RefreshCw className={cn("size-4", busy && "animate-spin")} />
            Dry run
          </Button>
          <Button
            onClick={() => start(false)}
            disabled={busy || !tradingAllowed}
            title={tradingAllowed ? undefined : gateReason}
            className="gap-1.5"
          >
            <Play className="size-4" />
            Start rebalance
          </Button>
        </div>
      </div>

      {busy && !proposal && (
        <Card className="gap-2 p-4">
          <div className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2">
              <Loader2 className="size-4 animate-spin text-primary" />
              {job?.message || "Starting…"}
            </span>
            <span className="flex items-center gap-3 text-muted-foreground">
              <span className="tabular-nums">
                {job?.elapsedSeconds.toFixed(0)}s
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => job && jobsApi.cancel(job.id)}
              >
                <X className="size-3.5" />
              </Button>
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500"
              style={{ width: `${Math.round((job?.progress ?? 0) * 100)}%` }}
            />
          </div>
        </Card>
      )}

      {proposal && (
        <>
          {!proposal.marketOpen && (
            <Card className="border-unknown/50 bg-unknown/10 p-3">
              <div className="flex items-start gap-2.5 text-sm">
                <Clock className="mt-0.5 size-4 shrink-0 text-unknown" />
                <p className="text-muted-foreground">
                  <span className="font-medium text-unknown">
                    The US market is closed.
                  </span>{" "}
                  Orders placed now sit unfilled until the next session, and
                  entry limits priced off today&rsquo;s close may be stale by
                  then. Queueing deliberately is a legitimate choice — just not
                  an accidental one.
                </p>
              </div>
            </Card>
          )}

          <Card className="gap-4 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-medium">Proposed rebalance</h2>
                <p className="text-sm text-muted-foreground">
                  Signal <span className="font-mono">{proposal.signal}</span> ·
                  top-{proposal.top_n} · NetLiq{" "}
                  {fmtUsd(proposal.net_liq_usd, 0)}
                </p>
              </div>
              <Badge
                variant="outline"
                className="gap-1.5 border-unknown/40 text-unknown"
              >
                <Clock className="size-3" />
                {proposal.expiresInSeconds}s to decide
              </Badge>
            </div>

            <div className="grid gap-3 sm:grid-cols-3">
              <Bucket
                title="Sell"
                tone="loss"
                empty="Nothing to exit"
                rows={proposal.sells.map((s) => ({
                  key: s.symbol,
                  main: s.symbol,
                  detail: `${fmtQty(s.quantity)} sh — dropped from top-${proposal.top_n}`,
                }))}
              />
              <Bucket
                title="Hold"
                tone="neutral"
                empty="Nothing held"
                rows={proposal.holds.map((s) => ({
                  key: s.symbol,
                  main: s.symbol,
                  detail: `${fmtQty(s.quantity)} sh — still in target`,
                }))}
              />
              <Bucket
                title="Buy"
                tone="profit"
                empty="Nothing to enter"
                rows={proposal.buys.map((b) => ({
                  key: b.symbol,
                  main: b.symbol,
                  detail:
                    b.qty > 0
                      ? `${b.qty} sh @ ~${fmtPrice(b.entry)} · stop ${fmtPrice(b.stop)}`
                      : "size 0 — RiskGuard will block",
                  warn: b.qty <= 0,
                }))}
              />
            </div>

            <div className="rounded-md border border-border">
              <p className="border-b border-border px-3 py-2 text-xs uppercase tracking-wide text-muted-foreground">
                Ranking — {proposal.rankLabel}
              </p>
              <ul className="divide-y divide-border/50">
                {proposal.ranking.map((r, i) => (
                  <li
                    key={r.ticker}
                    className={cn(
                      "flex items-center justify-between px-3 py-1.5 text-sm",
                      // The boundary pair is where the decision actually is.
                      (i === proposal.top_n - 1 || i === proposal.top_n) &&
                        "border-l-2 border-l-unknown"
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span className="w-5 tabular-nums text-xs text-muted-foreground">
                        {i + 1}
                      </span>
                      <span className="font-mono">{r.ticker}</span>
                      {r.inTop && (
                        <Badge
                          variant="outline"
                          className="border-primary/40 text-[10px] text-primary"
                        >
                          TOP
                        </Badge>
                      )}
                    </span>
                    <span
                      className={cn(
                        "tabular-nums",
                        r.value > 0 ? "text-profit" : "text-loss"
                      )}
                    >
                      {fmtPct(r.value)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2">
              <p className="mr-auto text-xs text-muted-foreground">
                Approving places these orders through RiskGuard: exits first to
                free position headroom, then bracket entries with GTC stops.
              </p>
              <Button
                variant="ghost"
                onClick={() => decide(false)}
                disabled={deciding}
              >
                Decline
              </Button>
              <Button onClick={() => decide(true)} disabled={deciding}>
                {deciding && <Loader2 className="mr-1.5 size-4 animate-spin" />}
                Approve &amp; place
                <ArrowRight className="ml-1 size-4" />
              </Button>
            </div>
          </Card>
        </>
      )}

      {job && (job.log?.length ?? 0) > 0 && (
        <Card className="gap-2 p-4">
          <div className="flex items-center gap-2">
            {job.status === "done" ? (
              <CheckCircle2 className="size-4 text-profit" />
            ) : job.status === "failed" ? (
              <AlertTriangle className="size-4 text-loss" />
            ) : (
              <Loader2 className="size-4 animate-spin text-primary" />
            )}
            <h2 className="font-medium">Run log</h2>
            <Badge variant="outline" className="text-[10px] text-muted-foreground">
              {job.status}
            </Badge>
          </div>
          {job.error && <p className="text-sm text-loss">{job.error}</p>}
          <pre className="max-h-72 overflow-auto rounded bg-background/60 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
            {job.log?.join("\n")}
          </pre>
        </Card>
      )}
    </div>
  );
}

function Bucket({
  title,
  tone,
  rows,
  empty,
}: {
  title: string;
  tone: "loss" | "profit" | "neutral";
  rows: { key: string; main: string; detail: string; warn?: boolean }[];
  empty: string;
}) {
  const toneClass = {
    loss: "text-loss",
    profit: "text-profit",
    neutral: "text-muted-foreground",
  }[tone];
  return (
    <div className="rounded-md border border-border">
      <p
        className={cn(
          "border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wide",
          toneClass
        )}
      >
        {title} ({rows.length})
      </p>
      {rows.length === 0 ? (
        <p className="px-3 py-3 text-sm text-muted-foreground">{empty}</p>
      ) : (
        <ul className="divide-y divide-border/50">
          {rows.map((r) => (
            <li key={r.key} className="px-3 py-2">
              <div className="font-mono text-sm font-medium">{r.main}</div>
              <div
                className={cn(
                  "text-xs",
                  r.warn ? "text-unknown" : "text-muted-foreground"
                )}
              >
                {r.detail}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
