"use client";

/**
 * ftmo-kronos-panel.tsx — the signal-to-order surface for the FTMO venue.
 *
 * Two controls and one table. The controls arm the unattended runner and run
 * a read-only preview of exactly what it would do; the table is the ranking
 * with the proposed orders attached.
 *
 * The preview calls the SAME pipeline the runner uses (`ftmo_api.plan` ->
 * `ftmo_signal.plan_orders`), so what this screen shows and what fires
 * unattended cannot diverge. Nothing here ranks, sizes or computes a stop —
 * the moment the browser did any of that, there would be two implementations
 * of the risk maths and one of them would eventually be wrong.
 *
 * The arming dialog states the evidence position rather than presenting a
 * neutral switch. All four asset classes failed their IC screen on
 * 2026-08-03, and CLAUDE.md records running this anyway as a deliberate
 * exception to rule 5. A UI that hid that would be misrepresenting the
 * project's own findings at the exact moment it matters.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Play, Power, PowerOff } from "lucide-react";
import { toast } from "sonner";

import {
  ApiError,
  ftmo,
  jobs as jobsApi,
  type FtmoAutotradeState,
  type FtmoPlanResult,
  type Job,
} from "@/lib/api";
import { DASH, fmtPrice, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function FtmoKronosPanel() {
  const [state, setState] = useState<FtmoAutotradeState | null>(null);
  const [stateError, setStateError] = useState<string | null>(null);
  const [job, setJob] = useState<Job<FtmoPlanResult> | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // A failed fetch is tracked separately from "not loaded yet". Both leave
  // `state` null, but they are different claims and the UI must not present
  // either of them as "disarmed" — see the three-way branch on the button.
  const refresh = useCallback(() => {
    ftmo
      .autotrade()
      .then((s) => {
        setState(s);
        setStateError(null);
      })
      .catch((err) => {
        setState(null);
        setStateError(err instanceof ApiError ? err.message : String(err));
      });
  }, []);

  useEffect(refresh, [refresh]);
  useEffect(
    () => () => {
      if (pollRef.current) clearInterval(pollRef.current);
    },
    []
  );

  const setEnabled = async (enabled: boolean) => {
    setBusy(true);
    try {
      const res = await ftmo.setAutotrade(enabled);
      setState(res.autotrade);
      toast[enabled ? "warning" : "success"](
        enabled ? "FTMO autotrade ARMED" : "FTMO autotrade disabled",
        {
          description: enabled
            ? "Kronos will trade unattended from the next scheduled run. The rule engine, sizer and stop attachment stay enforced."
            : "The runner will place nothing. Open positions keep their server-side stops.",
        }
      );
    } catch (err) {
      toast.error("Could not change FTMO autotrade", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  const runPlan = async () => {
    try {
      const started = await ftmo.plan();
      setJob(started);
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const next = await jobsApi.get<FtmoPlanResult>(started.id);
          setJob(next);
          if (next.status !== "running" && next.status !== "queued") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            if (next.status === "failed") {
              toast.error("FTMO plan failed", {
                description: next.error ?? "",
              });
            }
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 1200);
    } catch (err) {
      toast.error("Could not start the plan", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    }
  };

  const running = job?.status === "running" || job?.status === "queued";
  const plan = job?.status === "done" ? job.result : null;
  const armed = state?.enabled ?? false;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-[10px] uppercase tracking-widest text-muted-foreground">
          Kronos → orders
        </h2>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={running}
            onClick={runPlan}
            title="Run the same pipeline the unattended runner uses, but stop before placing anything"
          >
            {running ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Play className="size-4" />
            )}
            <span className="ml-1.5">
              {running ? "Forecasting…" : "Preview plan"}
            </span>
          </Button>

          {/* Three states, not two. `state === null` means the arm status has
              not loaded yet — it is NOT "off". Rendering "Autotrade off"
              while the runner is armed is rule 1 in web/CLAUDE.md failing in
              its most dangerous direction: you would read the screen as
              "nothing is trading" while Kronos is placing orders every night.
              This is the same distinction the stop-protection column makes
              between unprotected and UNKNOWN. */}
          {state === null ? (
            <Button
              size="sm"
              variant="outline"
              disabled
              className="text-muted-foreground"
              onClick={refresh}
              title={
                stateError
                  ? `Could not read the arm status: ${stateError}`
                  : "Asking the backend whether the runner is armed"
              }
            >
              {stateError ? (
                <AlertTriangle className="size-4 text-unknown" />
              ) : (
                <Loader2 className="size-4 animate-spin" />
              )}
              <span className="ml-1.5">
                {stateError ? "Arm state unknown" : "Checking…"}
              </span>
            </Button>
          ) : armed ? (
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={() => setEnabled(false)}
              title="Stop unattended FTMO trading"
            >
              <PowerOff className="size-4" />
              <span className="ml-1.5">Disarm</span>
            </Button>
          ) : (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setConfirmOpen(true)}
              className="text-muted-foreground"
              title="Arm unattended Kronos trading on FTMO"
            >
              <Power className="size-4" />
              <span className="ml-1.5">Autotrade off</span>
            </Button>
          )}
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        {state === null ? (
          stateError
            ? `Could not read whether the runner is armed (${stateError}). This is missing information, not "disarmed" — check ftmo_launchd.log or trader_settings.json.`
            : "Waiting for the backend."
        ) : (
          <>
            {armed ? (
              <span className="font-medium text-destructive">ARMED</span>
            ) : (
              <span>Disarmed</span>
            )}
            {" · "}top {state.topN} · {state.riskPct}% risk per trade ·{" "}
            {state.rotationMarginPct} pt rotation margin ·{" "}
            {state.product} buffer {Math.round(state.bufferPct * 100)}%
            {state.dayState ? (
              <>
                {" · "}day start {fmtUsd(state.dayState.day_start_balance)} ·{" "}
                {state.dayState.trading_days} trading day
                {state.dayState.trading_days === 1 ? "" : "s"}
              </>
            ) : (
              <> · no day state yet (seeds on first run)</>
            )}
          </>
        )}
      </p>

      {running && (
        <div className="border hairline border-border bg-background px-3 py-2 text-xs">
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            {job?.message ?? "Running…"}
          </div>
          {job?.log && job.log.length > 0 && (
            <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-muted-foreground/80">
              {job.log.slice(-6).join("\n")}
            </pre>
          )}
        </div>
      )}

      {job?.status === "failed" && (
        <p className="border hairline border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {job.error ?? "The plan failed."}
        </p>
      )}

      {plan && <PlanView plan={plan} />}

      {!plan && !running && job?.status !== "failed" && (
        <p className="border hairline border-border bg-background px-3 py-6 text-center text-xs text-muted-foreground">
          No plan computed yet. A preview runs a full Kronos forecast over the
          FTMO universe and takes a few minutes.
        </p>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-5 text-unknown" />
              Arm unattended FTMO trading?
            </DialogTitle>
            <DialogDescription>
              Kronos will place real orders on the FTMO account with no
              approval prompt, on every scheduled run.
            </DialogDescription>
          </DialogHeader>

          <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
            <li>
              <span className="text-foreground">
                All four asset classes failed their IC screen
              </span>{" "}
              on 2026-08-03 (|t| ≤ 1.55 in every direction), and the matched
              momentum baseline failed all four too. Kronos has no demonstrated
              edge on any class this project has measured.
            </li>
            <li>
              Running it anyway is a deliberate exception to rule 5, recorded
              as such. It is an experiment, not a validated strategy.
            </li>
            <li>
              Autonomy removes the approval step, never a limit. The rule
              engine, the 1%-per-trade and portfolio risk caps, and the
              server-side stop attached at entry all stay enforced.
            </li>
            <li>
              The capital is simulated. The account can still fail — every
              FTMO limit is measured on equity including floating P&amp;L.
            </li>
          </ul>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => setEnabled(true)}
            >
              Arm FTMO autotrade
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function PlanView({ plan }: { plan: FtmoPlanResult }) {
  const target = new Set(plan.target);
  const totalRisk = plan.entries.reduce((a, e) => a + e.risk_at_stop, 0);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge
          variant={
            plan.verdict.posture === "OK" ? "outline" : "destructive"
          }
        >
          {plan.verdict.posture}
        </Badge>
        <span className="text-muted-foreground">
          equity {fmtUsd(plan.account.equity)} · day start{" "}
          {fmtUsd(plan.account.dayStartBalance)} · sample_count{" "}
          {plan.sampleCount}
        </span>
      </div>

      {plan.verdict.reasons.length > 0 && (
        <p className="border hairline border-unknown/40 bg-unknown/5 px-3 py-2 text-xs">
          {plan.verdict.reasons.join("; ")}
        </p>
      )}

      {plan.gapIsNarrow && plan.rankGap !== null && (
        <p className="border hairline border-unknown/40 bg-unknown/5 px-3 py-2 text-xs">
          The gap between rank {plan.topN} and rank {plan.topN + 1} is{" "}
          {plan.rankGap.toFixed(2)} points. This project has measured Kronos&apos;s
          top-N flipping between two runs on identical data at about that
          distance — the selection at that boundary is close to a coin flip,
          and the {plan.rotationMarginPct}-point rotation margin is what is
          holding it steady, not the signal.
        </p>
      )}

      <div className="overflow-x-auto border hairline border-border">
        <table className="w-full text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              <Th>#</Th>
              <Th>Symbol</Th>
              <Th>Class</Th>
              <Th right>Predicted</Th>
              <Th right>ATR</Th>
              <Th>State</Th>
            </tr>
          </thead>
          <tbody>
            {plan.ranked.map((r, i) => {
              const held = plan.held.includes(r.symbol);
              const inTarget = target.has(r.symbol);
              return (
                <tr key={r.symbol} className="border-t hairline border-border">
                  <Td mono>{i + 1}</Td>
                  <Td mono className={inTarget ? "font-medium" : ""}>
                    {r.symbol}
                  </Td>
                  <Td>{r.assetClass}</Td>
                  <Td right mono>
                    {r.predictedReturnPct >= 0 ? "+" : ""}
                    {r.predictedReturnPct.toFixed(2)}%
                  </Td>
                  <Td right mono>
                    {fmtPrice(r.atr)}
                  </Td>
                  {/* Plan state is UI chrome, not P&L, so it deliberately
                      does NOT use --profit/--loss. globals.css reserves those
                      hues for money: a green number on this screen means
                      money made, and "this symbol is being entered" must not
                      compete with that. */}
                  <Td>
                    {inTarget && held ? (
                      <span className="text-muted-foreground">hold</span>
                    ) : inTarget ? (
                      <span className="font-medium text-foreground">enter</span>
                    ) : held ? (
                      <span className="font-medium text-destructive">exit</span>
                    ) : (
                      <span className="text-muted-foreground/50">{DASH}</span>
                    )}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {plan.entries.length > 0 ? (
        <div className="space-y-1.5">
          <h3 className="text-[10px] uppercase tracking-widest text-muted-foreground">
            Proposed entries
          </h3>
          {plan.entries.map((e) => (
            <div
              key={e.symbol}
              className="flex flex-wrap items-baseline justify-between gap-2 border hairline border-border bg-background px-3 py-2 font-mono text-xs"
            >
              <span>
                {e.side} {e.symbol} vol {e.volume.toLocaleString()}
              </span>
              <span className="text-muted-foreground">
                entry {fmtPrice(e.entry_price)} · stop {fmtPrice(e.stop_price)}{" "}
                · risk {fmtUsd(e.risk_at_stop)}
              </span>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            Total risk if every stop fills: {fmtUsd(totalRisk)}
          </p>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          No entries proposed.
        </p>
      )}

      {plan.skipped.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {plan.skipped.map((s, i) => (
            <li key={i}>skipped: {s}</li>
          ))}
        </ul>
      )}

      {plan.rejectedSymbols.length > 0 && (
        <ul className="space-y-1 text-xs text-destructive">
          {plan.rejectedSymbols.map((r) => (
            <li key={r.symbol}>
              {r.symbol}: {r.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={cn(
        "px-3 py-1.5 text-left font-normal uppercase tracking-wider text-[10px]",
        right && "text-right"
      )}
    >
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
      className={cn(
        "px-3 py-1.5",
        right && "text-right",
        mono && "font-mono",
        className
      )}
    >
      {children}
    </td>
  );
}
