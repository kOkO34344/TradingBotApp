"use client";

/**
 * kronos/page.tsx — the forecast section.
 *
 * Framing is deliberate and matches what the project's own evidence says.
 * Kronos is the focus signal by owner decision, not because it measured an
 * edge: Spearman IC 0.036 and a 50.0% directional hit rate daily, IC -0.081
 * and 46.4% hourly — worse than the momentum sort it replaced on the only
 * head-to-head screen. The page says so once, near the top, and then gets on
 * with showing the numbers. It does not repeat the point or editorialise
 * further; the owner knows, and the header exists so a *third* party reading
 * the screen isn't misled.
 *
 * The real work here is the spread. A single forecast is one draw; running
 * several and showing how far apart they land is what turns "the model says
 * BUY GOOGL" into "the model says GOOGL somewhere between -13% and 0%, and
 * whether it makes the cut is a coin flip".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Ban,
  Brain,
  Info,
  Loader2,
  Play,
  Shuffle,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  ApiError,
  jobs as jobsApi,
  kronos,
  type Job,
  type KronosResult,
  type KronosStat,
  type MonteCarloResult,
} from "@/lib/api";
import { DASH, fmtPct, fmtPrice, fmtTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MAX_DRAWN_PATHS, FanChart, ForecastChart } from "@/components/chart/forecast-chart";

export function KronosScreen() {
  const [job, setJob] = useState<Job<KronosResult> | null>(null);
  const [mcJob, setMcJob] = useState<Job<MonteCarloResult> | null>(null);
  const [draws, setDraws] = useState(3);
  const [selected, setSelected] = useState<string | null>(null);
  const [mcTicker, setMcTicker] = useState("");
  const [mcPaths, setMcPaths] = useState(12);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Load whatever the last completed run was, so reopening the page doesn't
  // mean paying for another inference. BOTH panels restore — the forecast and
  // the Monte Carlo are each a couple of minutes of GPU time, and losing one
  // of them on a refresh while the other survived was an inconsistency you
  // would only notice by paying for it twice.
  useEffect(() => {
    kronos
      .latest()
      .then(({ job: last, running }) => {
        if (running) setJob(running as Job<KronosResult>);
        else if (last) setJob(last);
      })
      .catch(() => {});
    kronos
      .latest<MonteCarloResult>("kronos-mc")
      .then(({ job: last, running }) => {
        if (running) setMcJob(running as Job<MonteCarloResult>);
        else if (last) setMcJob(last);
      })
      .catch(() => {});
  }, []);

  const poll = useCallback(
    (id: string, setter: (j: Job<never>) => void) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const next = await jobsApi.get<never>(id);
          setter(next);
          if (next.status !== "running" && next.status !== "queued") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            if (next.status === "failed") {
              toast.error("Kronos run failed", { description: next.error ?? "" });
            }
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 1200);
    },
    []
  );

  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  const start = async () => {
    try {
      const started = await kronos.run({ draws });
      setJob(started);
      poll(started.id, setJob as never);
    } catch (err) {
      toast.error("Could not start", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    }
  };

  const startMonteCarlo = async () => {
    const ticker = (mcTicker || selected || "").trim().toUpperCase();
    if (!ticker) return;
    try {
      const started = await kronos.monteCarlo(ticker, mcPaths);
      setMcJob(started);
      poll(started.id, setMcJob as never);
    } catch (err) {
      toast.error("Could not start Monte Carlo", {
        description: err instanceof ApiError ? err.message : String(err),
      });
    }
  };

  const busy = job?.status === "running" || job?.status === "queued";
  const result = job?.status === "done" ? job.result : null;
  const mcBusy = mcJob?.status === "running" || mcJob?.status === "queued";
  const mcResult = mcJob?.status === "done" ? mcJob.result : null;

  const chartTicker = selected ?? result?.stats[0]?.ticker ?? null;
  const chart = chartTicker ? result?.charts[chartTicker] : null;

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Brain className="size-6 text-primary" />
            Kronos forecast
          </h1>
          <p className="text-sm text-muted-foreground">
            Foundation-model price forecasts, run several times so the
            sampling noise is visible.
          </p>
        </div>

        <div className="flex items-end gap-2">
          <div className="space-y-1">
            <Label htmlFor="draws" className="text-xs">
              Draws
            </Label>
            <Input
              id="draws"
              type="number"
              min={1}
              max={10}
              value={draws}
              onChange={(e) => setDraws(Number(e.target.value))}
              className="w-20 tabular-nums"
            />
          </div>
          {busy ? (
            <Button
              variant="outline"
              onClick={() => job && jobsApi.cancel(job.id)}
              className="gap-1.5"
            >
              <X className="size-4" />
              Cancel
            </Button>
          ) : (
            <Button onClick={start} className="gap-1.5">
              <Play className="size-4" />
              Run forecast
            </Button>
          )}
        </div>
      </div>

      {/* Stated once, factually, near the top. */}
      <Card className="border-border/70 bg-muted/30 p-3">
        <div className="flex items-start gap-2.5 text-sm text-muted-foreground">
          <Info className="mt-0.5 size-4 shrink-0" />
          <p>
            Kronos is this project&rsquo;s focus signal by decision, not by
            measured edge. Walk-forward from July 2024: Spearman IC{" "}
            <span className="font-mono">0.036</span>, directional hit rate{" "}
            <span className="font-mono">50.0%</span> on 20-day returns. At
            hourly cadence it scored <span className="font-mono">-0.081</span>{" "}
            / <span className="font-mono">46.4%</span>, below the trailing-return
            sort it replaced. Read these forecasts as a research direction, not
            a validated edge.
          </p>
        </div>
      </Card>

      {/* ------------------------------------------------------- progress */}
      {job && (job.status !== "done" || busy) && (
        <JobProgress job={job} />
      )}
      {job?.status === "failed" && (
        <Card className="border-loss/40 bg-loss/5 p-4">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-loss" />
            <div className="min-w-0">
              <p className="font-medium text-loss">Run failed</p>
              <p className="text-sm text-muted-foreground">{job.error}</p>
              {job.log && job.log.length > 0 && (
                <pre className="mt-2 max-h-40 overflow-auto rounded bg-background/60 p-2 text-xs text-muted-foreground">
                  {job.log.join("\n")}
                </pre>
              )}
            </div>
          </div>
        </Card>
      )}

      {result && (
        <>
          {/* ------------------------------------------------ gap warning */}
          {result.gapWarning && (
            <Card className="border-unknown/50 bg-unknown/10 p-4">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="mt-0.5 size-5 shrink-0 text-unknown" />
                <div>
                  <p className="font-semibold text-unknown">
                    Top-{result.topN} boundary is inside the noise
                  </p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {result.gapWarning}
                  </p>
                </div>
              </div>
            </Card>
          )}

          <div className="flex flex-wrap gap-3 text-sm">
            <Meta label="Generated" value={fmtTime(result.generatedAt)} />
            <Meta label="Draws" value={String(result.draws)} />
            <Meta label="sample_count" value={String(result.sampleCount)} />
            <Meta label="Horizon" value={`${result.predLen} trading days`} />
            <Meta
              label="Rank changes per draw"
              value={`${result.rankChangesPerDraw} of ${result.stats.length}`}
              hint="How many tickers landed in a different rank between draws on identical data. Anything above zero means the ordering is partly noise."
            />
            {result.skipped.length > 0 && (
              <Meta
                label="Skipped"
                value={result.skipped.join(", ")}
                hint="Not enough daily history for the model's lookback."
              />
            )}
          </div>

          {/* --------------------------------------------------- ranking */}
          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-4 py-2.5 text-left font-medium">#</th>
                    <th className="px-4 py-2.5 text-left font-medium">Ticker</th>
                    <th className="px-4 py-2.5 text-right font-medium">
                      Mean predicted
                    </th>
                    <th className="px-4 py-2.5 text-right font-medium">Range</th>
                    <th className="px-4 py-2.5 text-left font-medium">
                      Spread across draws
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {result.stats.map((s, i) => (
                    <RankRow
                      key={s.ticker}
                      stat={s}
                      topN={result.topN}
                      isBoundary={i === result.topN - 1 || i === result.topN}
                      maxSpread={Math.max(
                        ...result.stats.map((x) => x.spreadPct),
                        0.01
                      )}
                      selected={chartTicker === s.ticker}
                      onSelect={() => setSelected(s.ticker)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* ----------------------------------------------------- chart */}
          {chart && chartTicker && (
            <Card className="gap-2 p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="font-medium">
                  <span className="font-mono">{chartTicker}</span> — last draw
                </h2>
                <p className="text-sm text-muted-foreground">
                  {fmtPrice(chart.lastClose)} →{" "}
                  <span className="text-foreground">
                    {fmtPrice(chart.predictedClose)}
                  </span>{" "}
                  <span
                    className={
                      chart.predictedClose >= chart.lastClose
                        ? "text-profit"
                        : "text-loss"
                    }
                  >
                    (
                    {fmtPct(
                      (chart.predictedClose / chart.lastClose - 1) * 100
                    )}
                    )
                  </span>
                </p>
              </div>
              <ForecastChart
                history={chart.history}
                forecast={chart.forecast}
              />
              <p className="text-xs text-muted-foreground">
                Tinted bars are model output, not prices. This is one draw —
                the range column above shows where the other draws landed.
              </p>
            </Card>
          )}
        </>
      )}

      {/* ------------------------------------------------- monte carlo */}
      <Card className="gap-3 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 font-medium">
              <Shuffle className="size-4 text-primary" />
              Monte Carlo fan
            </h2>
            <p className="text-sm text-muted-foreground">
              Individual single-sample paths over a P10–P90 envelope, with the
              median on top. Not averaged — the spread is the point.
            </p>
          </div>
          <div className="flex items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="mc-ticker" className="text-xs">
                Ticker
              </Label>
              <Input
                id="mc-ticker"
                value={mcTicker}
                onChange={(e) => setMcTicker(e.target.value)}
                placeholder={chartTicker ?? "AAPL"}
                className="w-28 font-mono uppercase"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="mc-paths" className="text-xs">
                Paths
              </Label>
              <Input
                id="mc-paths"
                type="number"
                min={2}
                max={40}
                value={mcPaths}
                onChange={(e) => setMcPaths(Number(e.target.value))}
                className="w-20 tabular-nums"
              />
            </div>
            {mcBusy ? (
              <Button
                variant="outline"
                onClick={() => mcJob && jobsApi.cancel(mcJob.id)}
                className="gap-1.5"
              >
                <X className="size-4" />
                Cancel
              </Button>
            ) : (
              <Button
                variant="secondary"
                onClick={startMonteCarlo}
                disabled={!mcTicker && !chartTicker}
                className="gap-1.5"
              >
                <Play className="size-4" />
                Run
              </Button>
            )}
          </div>
        </div>

        {mcJob && mcBusy && <JobProgress job={mcJob} compact />}
        {mcJob?.status === "failed" && (
          <p className="text-sm text-loss">{mcJob.error}</p>
        )}

        {mcResult && (
          <>
            <div className="flex flex-wrap gap-4 text-sm">
              <Meta
                label="Median"
                value={fmtPct(mcResult.medianReturnPct)}
              />
              <Meta label="Mean" value={fmtPct(mcResult.meanReturnPct)} />
              <Meta
                label="P10 – P90"
                value={`${fmtPct(mcResult.p10ReturnPct)} … ${fmtPct(
                  mcResult.p90ReturnPct
                )}`}
                hint="80% of the sampled paths finished inside this band."
              />
              <Meta
                label="Paths up"
                value={fmtPct(mcResult.shareUp, 0, false)}
              />
              <Meta label="Paths" value={String(mcResult.paths)} />
            </div>
            <FanChart history={mcResult.history} series={mcResult.series} />
            <p className="text-xs text-muted-foreground">
              Each line is one <span className="font-mono">sample_count=1</span>{" "}
              draw for {mcResult.ticker}, coloured{" "}
              <span className="text-profit">green</span> if it finishes above
              today&apos;s close and <span className="text-loss">pink</span> if
              below. The shaded ribbon is P10–P90; the thick orange line is the
              median. Where the lines fan out, the model is not committing to a
              direction.
              {mcResult.series.length > MAX_DRAWN_PATHS && (
                <>
                  {" "}
                  Showing {MAX_DRAWN_PATHS} of {mcResult.series.length} paths
                  individually — the ribbon still covers all of them.
                </>
              )}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

function JobProgress({ job, compact = false }: { job: Job<unknown>; compact?: boolean }) {
  return (
    <Card className={cn("gap-2", compact ? "p-3" : "p-4")}>
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="flex items-center gap-2">
          {job.status === "cancelled" ? (
            <Ban className="size-4 text-unknown" />
          ) : (
            <Loader2 className="size-4 animate-spin text-primary" />
          )}
          {job.message || "Starting…"}
        </span>
        <span className="tabular-nums text-muted-foreground">
          {job.elapsedSeconds.toFixed(0)}s
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-primary transition-all duration-500"
          style={{ width: `${Math.round(job.progress * 100)}%` }}
        />
      </div>
      {job.log && job.log.length > 0 && (
        <pre className="max-h-32 overflow-auto rounded bg-background/60 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {job.log.slice(-8).join("\n")}
        </pre>
      )}
    </Card>
  );
}

function RankRow({
  stat,
  topN,
  isBoundary,
  maxSpread,
  selected,
  onSelect,
}: {
  stat: KronosStat;
  topN: number;
  isBoundary: boolean;
  maxSpread: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const inTop = stat.rank <= topN;
  return (
    <tr
      onClick={onSelect}
      className={cn(
        "cursor-pointer border-b border-border/50 last:border-0 hover:bg-accent/30",
        selected && "bg-accent/40",
        // The rank-N / rank-N+1 pair is where a rotation decision actually
        // gets made, so it is marked even when the gap is comfortable.
        isBoundary && "border-l-2 border-l-unknown"
      )}
    >
      <td className="px-4 py-2.5 tabular-nums text-muted-foreground">
        {stat.rank}
      </td>
      <td className="px-4 py-2.5">
        <span className="font-mono font-medium">{stat.ticker}</span>
        {inTop && (
          <Badge
            variant="outline"
            className="ml-2 border-primary/40 text-[10px] text-primary"
          >
            TOP {topN}
          </Badge>
        )}
      </td>
      <td
        className={cn(
          "px-4 py-2.5 text-right tabular-nums font-medium",
          stat.meanReturnPct > 0 ? "text-profit" : "text-loss"
        )}
      >
        {fmtPct(stat.meanReturnPct)}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums text-xs text-muted-foreground">
        {stat.draws.length > 1
          ? `${fmtPct(stat.minReturnPct)} … ${fmtPct(stat.maxReturnPct)}`
          : DASH}
      </td>
      <td className="px-4 py-2.5">
        {stat.draws.length > 1 ? (
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-unknown"
                style={{
                  width: `${Math.min(100, (stat.spreadPct / maxSpread) * 100)}%`,
                }}
              />
            </div>
            <span className="tabular-nums text-xs text-muted-foreground">
              {stat.spreadPct.toFixed(2)} pts
            </span>
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">
            single draw — no spread measured
          </span>
        )}
      </td>
    </tr>
  );
}

function Meta({
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
