"use client";

/**
 * night-band.tsx — the watch station's signature instrument.
 *
 * One session, 16:30 Sofia through 11:30 the next morning, as a single
 * horizontal band. It answers the question this whole app exists for: what
 * did the robot do while nobody was awake, and how close did it come to the
 * floor.
 *
 * THREE LANES, SHARING ONE TIME AXIS. They are separate because they are
 * measured on scales that differ by three orders of magnitude, and forcing
 * them onto one axis would make the interesting one invisible:
 *
 *   EQUITY     auto-scaled to its own range. Overnight moves are dollars on a
 *              $25,000 account; pinned to the limit scale they would be a
 *              flat line, and a flat line reads as "nothing happened" rather
 *              than "nothing much happened".
 *   RESERVOIR  the daily-loss budget at TRUE scale, thresholds marked. A
 *              nearly-empty bar is the correct and reassuring reading, and it
 *              would be a lie to auto-scale this one into looking dramatic.
 *   WAKEUPS    one cell per hourly firing. This is the lane that earns the
 *              component.
 *
 * WHAT DID NOT HAPPEN IS THE POINT. A slot the window was open for with no
 * audit record is a firing that never ran — the Mac was asleep — and it is
 * drawn as a struck magenta cell, not as a gap. 22 consecutive silent failures
 * went unnoticed for 19 hours on this project; a band that quietly omitted
 * them would draw a tidy line through a night when nothing was watching.
 *
 * Nothing here recomputes a threshold. Every number arrives from
 * `ftmo_rules.evaluate()` by way of the audit trail. Recomputing a limit in
 * the browser is how the dashboard and the engine end up disagreeing about
 * whether the account is safe.
 */

import { type FtmoSlot, type FtmoTimeline } from "@/lib/api";
import { DASH, fmtSigned, fmtUsd } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useShell } from "@/components/app-shell";

const HOUR_MS = 3_600_000;

export function NightBand() {
  // Read from the shell, not fetched here. The rail's MISSED lamp and this
  // band must never be able to disagree about the same audit trail.
  const { timeline: data, timelineError: error } = useShell();

  if (error) {
    return (
      <section className="plate p-4">
        <Lane>Night band</Lane>
        <p className="mt-2 text-sm text-loss">
          Couldn&apos;t read the audit trail: {error}
        </p>
      </section>
    );
  }

  if (!data) {
    return (
      <section className="plate p-4">
        <Lane>Night band</Lane>
        <p className="mt-2 text-sm text-muted-foreground">
          Reading the audit trail.
        </p>
      </section>
    );
  }

  const start = Date.parse(data.start);
  // The rail is 20 hourly cells and the session is 19 hours long, so the axis
  // runs one hour past the close and the close itself is marked. Both lanes
  // use this same domain, which is what keeps a dip in the equity trace
  // sitting over the firing that caused it.
  const domainEnd = start + data.slots.length * HOUR_MS;
  const span = domainEnd - start;
  const pctAt = (iso: string) =>
    ((Date.parse(iso) - start) / span) * 100;

  const equity = data.trace
    .filter((p) => p.equity !== null)
    .map((p) => ({ x: pctAt(p.at), y: p.equity as number, at: p.at }));

  const lastEval = data.trace.length ? data.trace[data.trace.length - 1] : null;
  const dailyUsed = lastEval?.dailyUsed ?? null;
  const { dailySoft, dailyFlatten, dailyHard } = data.limits;

  return (
    <section className="plate overflow-hidden">
      <BandHeader data={data} />

      <div className="space-y-4 px-4 pt-3 pb-4">
        <EquityLane points={equity} />
        <ReservoirLane
          used={dailyUsed}
          soft={dailySoft}
          flatten={dailyFlatten}
          hard={dailyHard}
        />
        <WakeupLane slots={data.slots} counts={data.counts} />
      </div>
    </section>
  );
}

function BandHeader({ data }: { data: FtmoTimeline }) {
  const fmt = (iso: string) =>
    new Date(iso).toLocaleString("en-GB", {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: data.timezone,
    });
  return (
    <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b hairline border-border bg-muted/30 px-4 py-2.5">
      <div className="flex items-baseline gap-3">
        <h2 className="silkscreen text-foreground">Night band</h2>
        <span className="tabular text-xs text-muted-foreground">
          {fmt(data.start)} → {fmt(data.end)}
        </span>
      </div>
      <span className="tabular text-[11px] text-muted-foreground">
        {data.timezone.replace("_", " ")}
      </span>
    </header>
  );
}

/**
 * Equity, auto-scaled to its own range.
 *
 * The range is stated in the corner rather than left implied, because an
 * auto-scaled axis makes a $2 move and a $2,000 move look identical. A trace
 * without its scale is decoration.
 */
function EquityLane({
  points,
}: {
  points: { x: number; y: number; at: string }[];
}) {
  if (points.length === 0) {
    return (
      <div>
        <Lane>Equity</Lane>
        <p className="mt-1.5 border hairline border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
          No evaluation was recorded in this session, so there is no equity to
          trace. The wakeup rail below is the whole story of the night.
        </p>
      </div>
    );
  }

  const ys = points.map((p) => p.y);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  // A dead-flat trace has no range to scale to. Centring it is the honest
  // rendering: the alternative divides by zero and draws the line at the top,
  // which reads as a gain.
  const flat = hi - lo < 1e-9;
  const yAt = (v: number) => (flat ? 50 : 100 - ((v - lo) / (hi - lo)) * 90 - 5);

  const d = points
    .map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${yAt(p.y)}`)
    .join(" ");
  const net = points[points.length - 1].y - points[0].y;

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Lane>Equity</Lane>
        <div className="flex items-baseline gap-3 tabular text-[11px]">
          <span className="text-muted-foreground">
            {flat ? fmtUsd(hi) : `${fmtUsd(lo)} – ${fmtUsd(hi)}`}
          </span>
          <span
            className={cn(
              net > 0 && "text-profit",
              net < 0 && "text-loss",
              net === 0 && "text-muted-foreground"
            )}
          >
            {points.length > 1 ? fmtSigned(net) : DASH}
          </span>
        </div>
      </div>
      <div className="relative mt-1.5 h-[76px] w-full bg-muted/25">
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          aria-hidden
        >
          <path
            d={d}
            fill="none"
            stroke="var(--primary)"
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          {points.map((p) => (
            <circle
              key={p.at}
              cx={p.x}
              cy={yAt(p.y)}
              r={2.5}
              fill="var(--primary)"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        {points.length === 1 && (
          <span className="absolute inset-y-0 right-2 flex items-center text-[10px] text-muted-foreground">
            one evaluation — no trace to draw
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * The daily-loss budget, at true scale.
 *
 * FTMO's published number is the one the account must never reach, so soft
 * (stop opening) and flatten (close everything) sit before it and are marked.
 * A bar showing only "used vs hard" would hide the two thresholds that
 * actually govern what the runner does.
 */
function ReservoirLane({
  used,
  soft,
  flatten,
  hard,
}: {
  used: number | null;
  soft: number | null;
  flatten: number | null;
  hard: number | null;
}) {
  const known = used !== null && hard !== null && hard > 0;
  const pct = known ? Math.max(0, Math.min(100, (used / hard) * 100)) : 0;
  const tone =
    !known || flatten === null || soft === null
      ? "bg-muted-foreground"
      : used >= flatten
        ? "bg-loss"
        : used >= soft
          ? "bg-unknown"
          : "bg-primary";

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Lane>Daily loss</Lane>
        <span className="tabular text-[11px] text-muted-foreground">
          {known ? (
            <>
              <span className="text-foreground">{fmtUsd(used)}</span> used of{" "}
              {fmtUsd(hard)}
            </>
          ) : (
            // Unknown is not zero. No evaluation in this session means the
            // rule engine never spoke, which is a different claim from "no
            // loss" and must not be able to read as one.
            <>no evaluation this session — usage {DASH}</>
          )}
        </span>
      </div>
      <div className="relative mt-1.5 h-2.5 w-full bg-muted/60">
        <div
          className={cn("absolute inset-y-0 left-0 transition-[width]", tone)}
          style={{ width: `${pct}%` }}
        />
        {known && soft !== null && (
          <Threshold at={(soft / hard) * 100} label="soft" />
        )}
        {known && flatten !== null && (
          <Threshold at={(flatten / hard) * 100} label="flatten" />
        )}
      </div>
      {known && (
        <div className="mt-1 flex justify-between tabular text-[10px] text-muted-foreground/80">
          <span>stop opening {fmtUsd(soft)}</span>
          <span>flatten {fmtUsd(flatten)}</span>
          <span>fail {fmtUsd(hard)}</span>
        </div>
      )}
    </div>
  );
}

function Threshold({ at, label }: { at: number; label: string }) {
  return (
    <span
      title={label}
      className="absolute -top-0.5 h-[14px] w-px bg-foreground/45"
      style={{ left: `${at}%` }}
    />
  );
}

/** One cell per hourly wakeup. The lane that earns the component. */
function WakeupLane({
  slots,
  counts,
}: {
  slots: FtmoSlot[];
  counts: FtmoTimeline["counts"];
}) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4">
        <Lane>Wakeups</Lane>
        <div className="flex items-baseline gap-3 tabular text-[11px]">
          <Count n={counts.ran} label="ran" className="text-primary" />
          {counts.forced > 0 && (
            <Count n={counts.forced} label="forced" className="text-primary/70" />
          )}
          <Count
            n={counts.missed}
            label="missed"
            className={counts.missed > 0 ? "text-unknown" : "text-muted-foreground"}
          />
          <Count n={counts.closed} label="closed" className="text-muted-foreground" />
        </div>
      </div>

      <div className="mt-1.5 flex h-9 w-full gap-px">
        {slots.map((s) => (
          <SlotCell key={s.at} slot={s} />
        ))}
      </div>

      <div className="mt-1 flex justify-between tabular text-[10px] text-muted-foreground/80">
        <span>{slots[0]?.label ?? DASH}</span>
        <span>{slots[slots.length - 1]?.label ?? DASH}</span>
      </div>

      {counts.missed > 0 && (
        <p className="mt-2 border-l-2 border-unknown/60 bg-unknown/5 py-1.5 pl-2.5 text-[11px] text-unknown">
          {counts.missed} scheduled firing{counts.missed === 1 ? "" : "s"} left
          no audit record. The runner was due and did not run — usually the Mac
          asleep on battery. Nothing was watching the account for{" "}
          {counts.missed} hour{counts.missed === 1 ? "" : "s"}.
        </p>
      )}
    </div>
  );
}

function SlotCell({ slot }: { slot: FtmoSlot }) {
  const traded = slot.entries.length + slot.exits.length > 0;
  const detail = [
    `${slot.label} — ${slot.state}`,
    slot.reason,
    slot.entries.length ? `entries: ${slot.entries.join(", ")}` : "",
    slot.exits.length ? `exits: ${slot.exits.join(", ")}` : "",
    slot.firings > 1 ? `${slot.firings} firings in this hour` : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div
      title={detail}
      className={cn(
        "relative flex-1 min-w-0 rounded-[1px]",
        slot.state === "ran" && "bg-primary/85",
        slot.state === "forced" &&
          "border hairline border-primary/70 bg-primary/15",
        slot.state === "missed" &&
          "border hairline border-unknown/70 bg-unknown/10 annunciator-lit",
        slot.state === "closed" && "bg-muted/50"
      )}
    >
      {/* A missed slot is struck through: it is not an empty hour, it is an
          hour that was supposed to be covered and was not. */}
      {slot.state === "missed" && (
        <svg className="absolute inset-0 h-full w-full" aria-hidden>
          <line
            x1="0"
            y1="100%"
            x2="100%"
            y2="0"
            stroke="var(--unknown)"
            strokeWidth={1}
            opacity={0.55}
          />
        </svg>
      )}
      {traded && (
        <span className="absolute inset-x-0 bottom-0.5 text-center text-[9px] leading-none text-primary-foreground/90 mix-blend-luminosity">
          {slot.entries.length > 0 && `+${slot.entries.length}`}
          {slot.exits.length > 0 && ` −${slot.exits.length}`}
        </span>
      )}
    </div>
  );
}

function Count({
  n,
  label,
  className,
}: {
  n: number;
  label: string;
  className?: string;
}) {
  return (
    <span className={className}>
      {n} <span className="text-muted-foreground/70">{label}</span>
    </span>
  );
}

function Lane({ children }: { children: React.ReactNode }) {
  return <h3 className="silkscreen">{children}</h3>;
}
