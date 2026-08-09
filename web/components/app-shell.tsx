"use client";

/**
 * app-shell.tsx — the frame: tab strip, annunciator rail, kill switch.
 *
 * FOUR SCREENS (2026-08-09). Watch / Signal / Market / Ledger name what you
 * came to do. It was eight, three of them a dimmed IBKR section for a venue
 * that placed no orders; that venue was removed the same day and there is no
 * second broker left for this shell to reason about.
 *
 * THE ANNUNCIATOR RAIL replaces a row of badges, and follows the aviation
 * convention it borrows from: a legend that is DARK has nothing to say. Only
 * a lit legend means something, so a quiet rail is a quiet account and it
 * reads at a glance from across the room. It distinguishes caution (magenta)
 * from warning (red) rather than collapsing both into "bad" — the same
 * distinction rule 2 of web/CLAUDE.md makes between UNKNOWN and UNPROTECTED,
 * for the same reason: they call for different actions.
 *
 * THE HEADER REPORTS ONE VENUE, and the lesson from when it reported two is
 * worth keeping: a retired broker's socket in the primary pill meant a healthy
 * dashboard permanently displayed `ConnectionRefusedError [Errno 61] ... 4002`
 * on every screen while the venue that was working went unmentioned. Report
 * the thing that can trade.
 *
 * The session timeline is fetched HERE and passed down, not fetched by the
 * night band. The rail's MISSED lamp and the band read the same audit trail,
 * and two independent polls of it could disagree — the lamp reading all-clear
 * over a band showing a hole is exactly the kind of contradiction this project
 * has been bitten by before.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { Activity, BookOpen, Gauge, LineChart, Moon, Sun } from "lucide-react";

import {
  api,
  ftmo as ftmoApi,
  type FtmoTimeline,
  type Status,
} from "@/lib/api";
import { useFtmoStream, type FtmoPosition, type FtmoVerdict } from "@/lib/use-ftmo";
import { cn } from "@/lib/utils";
import { DASH } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { KillSwitch } from "@/components/kill-switch";

const NAV = [
  { href: "/watch", label: "Watch", icon: Gauge, hint: "the account and the night" },
  { href: "/signal", label: "Signal", icon: Activity, hint: "Kronos and the plan" },
  { href: "/market", label: "Market", icon: LineChart, hint: "prices and indicators" },
  { href: "/ledger", label: "Ledger", icon: BookOpen, hint: "journal and evidence" },
];

interface ShellContext {
  status: Status | null;
  statusError: string | null;
  refreshStatus: () => void;
  /** Last night's session. `null` until the first read lands. */
  timeline: FtmoTimeline | null;
  timelineError: string | null;
}

const ShellCtx = createContext<ShellContext>({
  status: null,
  statusError: null,
  refreshStatus: () => {},
  timeline: null,
  timelineError: null,
});

export const useShell = () => useContext(ShellCtx);

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const ftmo = useFtmoStream();
  const [status, setStatus] = useState<Status | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [dark, setDark] = useState(true);
  const [timeline, setTimeline] = useState<FtmoTimeline | null>(null);
  const [timelineError, setTimelineError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .status()
      .then((s) => {
        if (cancelled) return;
        setStatus(s);
        setStatusError(null);
      })
      .catch((err: Error) => {
        if (!cancelled) setStatusError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  // The audit trail only changes when the runner fires, so once a minute is
  // ample — a tighter poll re-reads the same files ~20x for nothing.
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      ftmoApi
        .timeline()
        .then((t) => {
          if (cancelled) return;
          setTimeline(t);
          setTimelineError(null);
        })
        .catch((e: Error) => !cancelled && setTimelineError(e.message));
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  // "Is the backend there at all" is now a single question with a single
  // answer: /api/status touches no broker, so it succeeds whenever the
  // process is up. There is no second venue whose socket could confuse this.
  const backendUp = status !== null;

  const ftmoReady = Boolean(ftmo.snap?.connection.ready);
  const ftmoAccount = ftmo.snap?.account?.accountId ?? null;
  const ftmoEquity = ftmo.snap?.account?.equity ?? null;

  // Whether the FTMO runner is armed. Kept as `null` until known so the rail
  // never claims "disarmed" about a runner that may be live — the same rule
  // that stops an unknown stop rendering as an absent one.
  const [ftmoArmed, setFtmoArmed] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    ftmoApi
      .autotrade()
      .then((s) => !cancelled && setFtmoArmed(s.enabled))
      .catch(() => !cancelled && setFtmoArmed(null));
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  return (
    <ShellCtx.Provider
      value={{
        status,
        statusError,
        refreshStatus: () => setNonce((n) => n + 1),
        timeline,
        timelineError,
      }}
    >
      <div className="flex min-h-full flex-col">
        <header className="sticky top-0 z-40 border-b hairline border-border bg-sidebar/95 backdrop-blur">
          <div className="flex h-13 items-center gap-5 px-4 py-2">
            <Link href="/watch" className="shrink-0">
              <span className="silkscreen text-foreground">Watch station</span>
            </Link>

            <nav className="flex items-center gap-1 overflow-x-auto">
              {NAV.map(({ href, label, icon: Icon, hint }) => {
                const active =
                  pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    title={hint}
                    className={cn(
                      "rgb-split flex items-center gap-1.5 whitespace-nowrap rounded-sm px-2.5 py-1.5 text-sm",
                      active
                        ? "bg-accent text-accent-foreground"
                        : "text-muted-foreground hover:bg-accent/40 hover:text-foreground"
                    )}
                  >
                    <Icon className="size-3.5" />
                    <span className="hidden md:inline">{label}</span>
                  </Link>
                );
              })}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              <FtmoBadge account={ftmoAccount} equity={ftmoEquity} />
              {/* Deliberately NOT gated on any connection: a switch you cannot
                  reach when things are going wrong is not a switch. */}
              <KillSwitch
                armed={ftmoArmed}
                onChanged={() => setNonce((n) => n + 1)}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label="Toggle theme"
                onClick={() => setDark((d) => !d)}
              >
                {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
              </Button>
            </div>
          </div>

          <AnnunciatorRail
            backendUp={backendUp}
            ftmoLive={ftmo.live}
            ftmoReady={ftmoReady}
            ftmoError={ftmo.snap?.connection.error ?? statusError}
            armed={ftmoArmed}
            verdict={ftmo.snap?.verdict ?? null}
            positions={ftmo.snap?.positions ?? []}
            missed={timeline?.counts.missed ?? null}
          />

          {/* One condition the rail is not loud enough for.
              A lamp with a tooltip is right for "this is worth a look"; it is
              wrong for "nothing on this screen is real". A dead backend means
              the latter, so it gets a full-width strip and a sentence that
              says what to do about it.

              The paper-account banner that used to sit beside this one was
              IBKR's, and went with it. FTMO's Challenge capital is simulated
              by the venue, so there is no live/paper distinction for this app
              to police — the equivalent guard here is the arm toggle, which is
              in the header and lit on the rail. */}
          {!backendUp && (
            <Banner tone="loss">
              <strong className="font-medium">Trading API offline</strong> — no
              screen on this app has live data. Start it with{" "}
              <code className="tabular">./run_web.sh</code>.
            </Banner>
          )}
        </header>

        <main className="flex-1 min-h-0">{children}</main>
      </div>
    </ShellCtx.Provider>
  );
}

/**
 * The annunciator rail.
 *
 * Dark means nothing to say. Every lamp below is unlit in the ordinary case,
 * so the rail is quiet when the account is quiet and anything glowing is worth
 * walking over to read.
 *
 * `null` is a third state everywhere it can occur and is drawn magenta, never
 * dark: "we have not heard" must not be able to look like "all clear". A dark
 * BREACHED lamp on a dashboard that never reached the venue would be the most
 * dangerous pixel in this app.
 */
function AnnunciatorRail({
  backendUp,
  ftmoLive,
  ftmoReady,
  ftmoError,
  armed,
  verdict,
  positions,
  missed,
}: {
  backendUp: boolean;
  ftmoLive: boolean;
  ftmoReady: boolean;
  ftmoError: string | null | undefined;
  armed: boolean | null;
  verdict: FtmoVerdict | null;
  positions: FtmoPosition[];
  missed: number | null;
}) {
  const unprotected = positions.filter((p) => !p.protected).length;
  const unpriced = positions.filter((p) => p.mark === null).length;

  const streamState: LampState = !backendUp
    ? "warn"
    : ftmoLive
      ? "ok"
      : ftmoReady
        ? "caution"
        : "warn";

  return (
    <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-t hairline border-border bg-background/40 px-4 py-1.5">
      <Lamp
        state={streamState}
        label="stream"
        detail={
          !backendUp
            ? "The trading API isn't running. Start it with ./run_web.sh."
            : ftmoLive
              ? "cTrader frames are arriving."
              : ftmoReady
                ? "Connected, but the last frame is too old to believe."
                : (ftmoError ??
                   "No FTMO session — the venue or the backend is unreachable.")
        }
      />
      <Lamp
        state={armed === null ? "caution" : armed ? "ok" : "off"}
        breathe={armed === true}
        label={armed === false ? "disarmed" : "armed"}
        detail={
          armed === null
            ? "Couldn't read the arm toggle, so whether the runner fires is unknown."
            : armed
              ? "ftmo.autotrade.enabled is true — the runner trades unattended."
              : "ftmo.autotrade.enabled is false. The runner wakes and places nothing."
        }
      />
      <Lamp
        state={
          verdict === null
            ? "caution"
            : verdict.breached
              ? "warn"
              : verdict.canOpen
                ? "off"
                : "caution"
        }
        label={verdict?.breached ? "breached" : "blocked"}
        detail={
          verdict === null
            ? "The rule engine hasn't reported yet."
            : verdict.breached
              ? `Account breached: ${verdict.reasons.join("; ")}`
              : verdict.canOpen
                ? "The rule engine allows new positions."
                : `New positions blocked: ${verdict.reasons.join("; ")}`
        }
      />
      <Lamp
        state={verdict?.mustFlatten ? "warn" : "off"}
        label="flatten"
        detail={
          verdict?.mustFlatten
            ? "A limit says close everything now."
            : "No flatten condition."
        }
      />
      <Lamp
        state={unprotected > 0 ? "warn" : "off"}
        label="unprot"
        detail={
          unprotected > 0
            ? `${unprotected} open position(s) carry no stop.`
            : "Every open position carries a stop."
        }
      />
      <Lamp
        state={unpriced > 0 ? "caution" : "off"}
        label="unpriced"
        detail={
          unpriced > 0
            ? `${unpriced} position(s) have no quote, so floating P&L excludes them. Equity shown is incomplete, not flat.`
            : "Every position is priced."
        }
      />
      <Lamp
        state={missed === null || missed > 0 ? "caution" : "off"}
        label="missed"
        detail={
          missed === null
            ? "Couldn't read the audit trail, so missed firings are unknown."
            : missed > 0
              ? `${missed} scheduled firing(s) left no audit record this session — the runner was due and did not run.`
              : "Every scheduled firing this session left an audit record."
        }
      />
    </div>
  );
}

function Banner({
  tone,
  children,
}: {
  tone: "loss" | "unknown";
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "border-t hairline px-4 py-1.5 text-xs",
        tone === "loss" && "border-loss/40 bg-loss/12 text-loss",
        tone === "unknown" && "border-unknown/40 bg-unknown/10 text-unknown"
      )}
    >
      {children}
    </div>
  );
}

type LampState = "off" | "ok" | "caution" | "warn";

function Lamp({
  state,
  label,
  detail,
  breathe = false,
}: {
  state: LampState;
  label: string;
  detail: string;
  /** Pulse the dot. Reserved for ARMED — "the robot is live right now" is the
   *  one state worth a heartbeat; a rail of pulsing lamps would be noise. */
  breathe?: boolean;
}) {
  return (
    <span
      title={detail}
      className={cn(
        "silkscreen flex items-center gap-1.5 rounded-sm px-1.5 py-0.5",
        state !== "off" && "annunciator-lit",
        state !== "off" && "neon",
        state === "ok" && "bg-profit/10 text-profit",
        state === "caution" && "bg-unknown/10 text-unknown",
        state === "warn" && "bg-loss/15 text-loss",
        state === "off" && "text-muted-foreground/35"
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          breathe && state !== "off" && "lamp-breathe",
          state === "ok" && "bg-profit",
          state === "caution" && "bg-unknown",
          state === "warn" && "bg-loss",
          state === "off" && "bg-current opacity-50"
        )}
      />
      {label}
    </span>
  );
}

/**
 * The FTMO account and its live equity.
 *
 * Equity, not balance, and that is the point: every FTMO limit is measured on
 * equity INCLUDING floating P&L, so the account can fail a limit with no order
 * placed. Showing balance here would show the number that cannot breach
 * anything.
 *
 * Renders nothing rather than zero when there is no frame yet (rule 1).
 */
function FtmoBadge({
  account,
  equity,
}: {
  account: number | null;
  equity: number | null;
}) {
  if (!account) return null;
  return (
    <span
      className="hidden items-baseline gap-2 rounded-sm border hairline border-primary/35 px-2 py-1 sm:flex"
      title="FTMO Challenge account. Simulated capital — every limit is measured on equity including floating P&L."
    >
      <span className="silkscreen text-primary">FTMO</span>
      <span className="tabular text-xs">
        {equity === null
          ? DASH
          : equity.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
      </span>
    </span>
  );
}
