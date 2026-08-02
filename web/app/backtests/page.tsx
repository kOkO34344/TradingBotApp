"use client";

/**
 * backtests/page.tsx — what the strategies actually did.
 *
 * Two things this screen refuses to do, both of them project rules rather
 * than taste:
 *
 *  - It does not show one blended number. In-sample and out-of-sample are
 *    separate sections, out-of-sample first, each labelled with what it can
 *    and cannot support. A strategy's in-sample result is a description of
 *    the fitting window, not evidence.
 *  - It does not present a quoted result as a computed one. Findings that
 *    come from CLAUDE.md rather than from a CSV this API read are marked
 *    "recorded, not recomputed" with their source. Rule 4 is honest
 *    backtesting; a dashboard that blurred the two would undo it.
 *
 * Negative results get the same visual weight as positive ones. The
 * headline here is that most of these lost to buy-and-hold, and the screen
 * says so plainly.
 */

import { AlertTriangle, BookOpen, Minus, TrendingDown, TrendingUp } from "lucide-react";

import {
  api,
  type BacktestFinding as Finding,
  type BacktestsResponse,
} from "@/lib/api";
import { useFetch } from "@/lib/use-live";
import { DASH, fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

export default function BacktestsPage() {
  const data = useFetch<BacktestsResponse>(() => api.backtests(), []);

  const results = data.data?.results;
  const findings = data.data?.findings ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Backtests</h1>
        <p className="text-sm text-muted-foreground">
          After costs, against buy-and-hold and SPY, with the in-sample and
          out-of-sample windows kept apart.
        </p>
      </div>

      {/* ------------------------------------------------- strategy cards */}
      <div className="grid gap-3 md:grid-cols-2">
        {findings.map((f) => (
          <FindingCard key={f.name} finding={f} />
        ))}
      </div>

      {data.error && (
        <Card className="border-loss/40 bg-loss/5 p-4 text-sm text-loss">
          {data.error.message}
        </Card>
      )}

      {results?.error && (
        <Card className="border-unknown/40 bg-unknown/5 p-4 text-sm text-muted-foreground">
          {results.error}
        </Card>
      )}

      {/* --------------------------------------------------- per period */}
      {results?.periods.map((period) => (
        <Card key={period.key} className="gap-3 p-0">
          <div className="border-b border-border px-4 py-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-medium">{period.label}</h2>
              <Badge
                variant="outline"
                className={cn(
                  "text-[10px]",
                  period.key === "out_of_sample_2019_present"
                    ? "border-primary/40 text-primary"
                    : "text-muted-foreground"
                )}
              >
                {period.key === "out_of_sample_2019_present"
                  ? "EVIDENCE"
                  : "NOT EVIDENCE"}
              </Badge>
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {period.caveat}
            </p>
          </div>

          <div className="grid gap-3 px-4 sm:grid-cols-4">
            <Summary
              label="Avg strategy CAGR"
              value={fmtPct(period.avgStrategyCagr, 1, false)}
              tone={
                (period.avgStrategyCagr ?? 0) > (period.avgSpyCagr ?? 0)
                  ? "profit"
                  : "loss"
              }
            />
            <Summary
              label="Avg buy & hold"
              value={fmtPct(period.avgBuyHoldCagr, 1, false)}
            />
            <Summary label="SPY" value={fmtPct(period.avgSpyCagr, 1, false)} />
            <Summary
              label="Beat SPY"
              value={`${period.beatSpy} of ${period.tickers}`}
              tone={period.beatSpy === 0 ? "loss" : undefined}
            />
          </div>

          <div className="overflow-x-auto pb-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-y border-border text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-4 py-2 text-left font-medium">Ticker</th>
                  <th className="px-4 py-2 text-right font-medium">
                    Strategy CAGR
                  </th>
                  <th className="px-4 py-2 text-right font-medium">
                    Buy &amp; hold
                  </th>
                  <th className="px-4 py-2 text-right font-medium">SPY</th>
                  <th className="px-4 py-2 text-right font-medium">Sharpe</th>
                  <th className="px-4 py-2 text-right font-medium">Max DD</th>
                  <th className="px-4 py-2 text-right font-medium">Trades</th>
                  <th className="px-4 py-2 text-right font-medium">Win rate</th>
                </tr>
              </thead>
              <tbody>
                {period.rows.map((r) => {
                  const beatSpy =
                    r.strategy_cagr_pct !== null &&
                    r.spy_bh_cagr_pct !== null &&
                    r.strategy_cagr_pct > r.spy_bh_cagr_pct;
                  return (
                    <tr
                      key={r.ticker}
                      className="border-b border-border/50 last:border-0"
                    >
                      <td className="px-4 py-2 font-mono font-medium">
                        {r.ticker}
                      </td>
                      <td
                        className={cn(
                          "px-4 py-2 text-right tabular-nums font-medium",
                          beatSpy ? "text-profit" : "text-loss"
                        )}
                      >
                        {fmtPct(r.strategy_cagr_pct, 1, false)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtPct(r.bh_cagr_pct, 1, false)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtPct(r.spy_bh_cagr_pct, 1, false)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                        {r.strategy_sharpe?.toFixed(2) ?? DASH}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-loss">
                        {fmtPct(r.strategy_max_dd_pct, 1, false)}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                        {r.strategy_trades ?? DASH}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-muted-foreground">
                        {fmtPct(r.strategy_win_rate_pct, 0, false)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ))}

      {results && (
        <p className="text-xs text-muted-foreground">
          {results.strategy} · source <code>{results.source}</code>
        </p>
      )}
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const config = {
    beat: { icon: TrendingUp, cls: "text-profit", label: "Beat benchmark" },
    lost: { icon: TrendingDown, cls: "text-loss", label: "Lost to benchmark" },
    "no-edge": { icon: Minus, cls: "text-unknown", label: "No measurable edge" },
  }[finding.verdict];
  const Icon = config.icon;

  return (
    <Card className="gap-2 p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-medium leading-snug">{finding.name}</h2>
        <Badge
          variant="outline"
          className={cn("shrink-0 gap-1 text-[10px]", config.cls)}
        >
          <Icon className="size-3" />
          {config.label}
        </Badge>
      </div>

      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {finding.metrics.map((m) => (
          <div key={m.label}>
            <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {m.label}
            </div>
            <div className="font-medium tabular-nums">{m.value}</div>
          </div>
        ))}
      </div>

      <p className="text-sm text-muted-foreground">{finding.note}</p>

      {finding.status && (
        <p className="rounded border border-unknown/30 bg-unknown/5 px-2 py-1 text-xs text-unknown">
          {finding.status}
        </p>
      )}

      {/* Provenance is not a footnote here — it's the difference between a
          number this API computed and one it is quoting. */}
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        {finding.computed ? (
          <>
            <BookOpen className="size-3" />
            Computed from {finding.source}
          </>
        ) : (
          <>
            <AlertTriangle className="size-3 text-unknown" />
            Recorded, not recomputed — {finding.source}
          </>
        )}
      </div>
    </Card>
  );
}

function Summary({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "profit" | "loss";
}) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "text-lg font-semibold tabular-nums",
          tone === "profit" && "text-profit",
          tone === "loss" && "text-loss"
        )}
      >
        {value}
      </div>
    </div>
  );
}
