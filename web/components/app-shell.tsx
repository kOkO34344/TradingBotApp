"use client";

/**
 * app-shell.tsx — nav, the account banner, and the kill switch.
 *
 * The banner is not decoration. Koko's rule 1 is "paper before real money",
 * and the guardrail chosen for this UI is that trading controls refuse to
 * render unless the backend has verified a real paper account id (starts
 * with 'D') — not merely a paper-looking port. That verdict lives here and
 * is passed down through `useAccountGate`, so no screen can decide for
 * itself that it's safe to show a trade button.
 *
 * THE HEADER REPORTS FTMO, NOT IBKR (changed 2026-08-07). FTMO is the venue
 * that trades; IBKR is retired in place and its web connection is off by
 * default. Reporting a retired venue's socket in the primary pill meant a
 * healthy dashboard permanently displayed
 * `ConnectionRefusedError [Errno 61] ... 4002`, on every screen, while the
 * venue that was actually working went unmentioned.
 *
 * The FTMO pill distinguishes three states on purpose:
 *   live      — the venue socket is open and frames are arriving
 *   degraded  — connected once, but the last frame is too old to believe
 *   offline   — no session (the backend or the venue is unreachable)
 * Collapsing these into "disconnected" would hide which thing to go fix.
 *
 * IBKR keeps a SECOND, muted chip, and only where it is relevant. "Retired,
 * deliberately not connected" is a state, not a fault, and must never be
 * painted in the failure colour — that is the same error class as rendering
 * an unknown stop as an absent one.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  BookOpen,
  Brain,
  LayoutDashboard,
  Moon,
  Power,
  Repeat,
  Sun,
  Wallet, Gauge,
} from "lucide-react";

import { api, ftmo as ftmoApi, type Status } from "@/lib/api";
import { useLive } from "@/lib/use-live";
import { useFtmoStream } from "@/lib/use-ftmo";
import { cn } from "@/lib/utils";
import { DASH } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KillSwitch } from "@/components/kill-switch";

// FTMO leads, then the venue-neutral screens, then the IBKR ones — which are
// marked `ibkr: true` so they can be dimmed when that venue's web connection
// is off. They are dimmed rather than hidden: the three IBKR positions are
// still open and still real, and a screen you cannot find is a worse way to
// say "retired" than a screen that tells you so when you open it.
const NAV = [
  { href: "/ftmo", label: "FTMO", icon: Gauge },
  { href: "/charts", label: "Charts", icon: BarChart3 },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/kronos", label: "Kronos", icon: Brain },
  { href: "/backtests", label: "Backtests", icon: Activity },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, ibkr: true },
  { href: "/positions", label: "Positions", icon: Wallet, ibkr: true },
  { href: "/rebalance", label: "Rebalance", icon: Repeat, ibkr: true },
];

interface ShellContext {
  status: Status | null;
  /** True only when the backend verified a paper account. Gates every control. */
  tradingAllowed: boolean;
  gateReason: string;
  refreshStatus: () => void;
}

const ShellCtx = createContext<ShellContext>({
  status: null,
  tradingAllowed: false,
  gateReason: "Still checking the account.",
  refreshStatus: () => {},
});

export const useShell = () => useContext(ShellCtx);

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const live = useLive();
  const ftmo = useFtmoStream();
  const [status, setStatus] = useState<Status | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const [dark, setDark] = useState(true);

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
  }, [nonce, live.revisions.orders, live.connection?.connected]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  /**
   * Screens whose data comes from FTMO, so a dead IB Gateway says nothing
   * about whether they work.
   *
   * `/charts` joined this list on 2026-08-07 when it moved off `/api/bars`.
   * Kept as an explicit list rather than a `startsWith("/ftmo")` check
   * precisely because that check silently stopped being the right question
   * the moment a non-`/ftmo` route started reading from the venue — the
   * banner would have claimed nothing would load on a screen that was
   * loading fine.
   */
  const isFtmoBacked = (path: string) =>
    path.startsWith("/ftmo") || path.startsWith("/charts");

  const conn = live.connection ?? status?.connection ?? null;
  const backendUp = live.socketOpen || status !== null;
  const gatewayUp = conn?.connected ?? false;
  const paper = conn?.paper ?? false;

  const tradingAllowed = Boolean(backendUp && gatewayUp && paper);
  const gateReason = !backendUp
    ? "The trading API isn't running. Start it with ./run_web.sh."
    : !gatewayUp
      ? conn?.error ?? "Not connected to IB Gateway."
      : !paper
        ? `Account ${conn?.account ?? "unknown"} is not a verified paper account. Controls are disabled.`
        : "";

  const connState: "live" | "degraded" | "offline" = !backendUp
    ? "offline"
    : gatewayUp
      ? "live"
      : "degraded";

  // IBKR's web connection is off by default (rule 9). When it is, the whole
  // IBKR apparatus — banner, pill, account badge — is a statement about a
  // venue nobody is asking anything of, so it is reported quietly and only
  // where it is relevant, never as a fault.
  const ibkrDisabled = conn?.disabled ?? false;

  // FTMO is the primary venue and therefore the primary pill.
  const ftmoState: "live" | "degraded" | "offline" = ftmo.snap?.connection.ready
    ? ftmo.live
      ? "live"
      : "degraded"
    : "offline";
  const ftmoAccount = ftmo.snap?.account?.accountId ?? null;
  const ftmoEquity = ftmo.snap?.account?.equity ?? null;

  // Whether the FTMO runner is armed, for the header kill switch. Kept as
  // `null` until known so the switch never claims "off" about a runner that
  // may be live — the same rule that stops an unknown stop rendering as an
  // absent one.
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
        tradingAllowed,
        gateReason,
        refreshStatus: () => setNonce((n) => n + 1),
      }}
    >
      <div className="flex min-h-full flex-col">
        <header className="sticky top-0 z-40 border-b border-border bg-sidebar/95 backdrop-blur">
          <div className="flex h-14 items-center gap-4 px-4">
            <Link href="/ftmo" className="flex items-center gap-2 shrink-0">
              <span className="grid size-7 place-items-center rounded bg-primary text-primary-foreground font-bold text-sm">
                T
              </span>
              <span className="font-semibold tracking-tight hidden sm:inline">
                TradingBot
              </span>
            </Link>

            <nav className="flex items-center gap-0.5 overflow-x-auto">
              {NAV.map(({ href, label, icon: Icon, ibkr }) => {
                const active =
                  pathname === href || pathname.startsWith(`${href}/`);
                const retired = Boolean(ibkr) && ibkrDisabled;
                return (
                  <Link
                    key={href}
                    href={href}
                    title={
                      retired
                        ? "IBKR is retired — this screen has no live data"
                        : undefined
                    }
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors whitespace-nowrap",
                      active
                        ? "bg-accent text-accent-foreground font-medium"
                        : "text-muted-foreground hover:text-foreground hover:bg-accent/50",
                      retired && !active && "opacity-45"
                    )}
                  >
                    <Icon className="size-4" />
                    <span className="hidden md:inline">{label}</span>
                  </Link>
                );
              })}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              {/* FTMO first — the venue that actually trades. */}
              <FtmoBadge account={ftmoAccount} equity={ftmoEquity} />
              <ConnectionPill
                state={ftmoState}
                label="FTMO"
                detail={
                  ftmoState === "live"
                    ? `cTrader · account ${ftmoAccount ?? "?"}`
                    : (ftmo.snap?.connection.error ??
                       "No FTMO session. The venue or the backend is unreachable.")
                }
              />

              {/* IBKR, only when it is something you could act on. Disabled is
                  a settled decision, not news, so it gets one muted chip. */}
              {ibkrDisabled ? (
                <Badge
                  variant="outline"
                  className="text-[10px] uppercase text-muted-foreground/70"
                  title={conn?.error ?? undefined}
                >
                  IBKR retired
                </Badge>
              ) : (
                <>
                  <AccountBadge
                    account={conn?.account ?? null}
                    paper={paper}
                    connState={connState}
                  />
                  <ConnectionPill
                    state={connState}
                    label="IBKR"
                    detail={
                      connState === "live"
                        ? `${conn?.host}:${conn?.port} · clientId ${conn?.clientId}`
                        : (conn?.error ?? statusError ?? "")
                    }
                  />
                </>
              )}
              {/* The kill switch follows the venue that can actually trade.
                  With IBKR off, cutting IBKR's runner would be theatre while
                  the FTMO runner fires unattended ~20 times a day.

                  It is deliberately NOT gated on any connection: a switch you
                  cannot reach when things are going wrong is not a switch. */}
              <KillSwitch
                venue={ibkrDisabled ? "ftmo" : "ibkr"}
                autotradeEnabled={
                  ibkrDisabled
                    ? (ftmoArmed ?? false)
                    : (status?.autotrade.enabled ?? false)
                }
                disabled={ibkrDisabled ? false : !tradingAllowed}
                disabledReason={gateReason}
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

          {/* Scope the IBKR banner to IBKR screens.
              Rule 9 retired IBKR for new orders, so a dead Gateway is an
              expected state, not an application fault. Showing it full-width
              on a screen that does not touch Gateway made a working app look
              broken and buried the one venue that can actually trade. It
              still shows loudly on every screen that DOES depend on Gateway,
              because there it is the reason nothing loads. */}
          {connState !== "live" && !ibkrDisabled && !isFtmoBacked(pathname) && (
          {connState !== "live" && !isFtmoBacked(pathname) && (
            <div
              className={cn(
                "px-4 py-1.5 text-xs border-t",
                connState === "offline"
                  ? "bg-loss/10 border-loss/30 text-loss"
                  : "bg-unknown/10 border-unknown/30 text-unknown"
              )}
            >
              <span className="font-medium">
                {connState === "offline"
                  ? "Trading API offline"
                  : "IB Gateway not connected"}
                :
              </span>{" "}
              {gateReason || "No data will load until this is resolved."}
            </div>
          )}

          {/* An IBKR screen while IBKR is switched off. Informational, not a
              failure colour: this is the configured state, and the screen is
              empty because nobody is asking Gateway anything — not because
              something broke. It also says where the venue's data DID go, so
              the answer isn't "the app is broken". */}
          {ibkrDisabled && !isFtmoBacked(pathname) && (
            <div className="px-4 py-1.5 text-xs border-t border-border bg-muted/40 text-muted-foreground">
              <span className="font-medium text-foreground">
                IBKR is retired (rule 9)
              </span>{" "}
              — the dashboard does not connect to IB Gateway, so this screen has
              no data. Its three open positions are still managed by{" "}
              <code>reflect_on_trades.py</code>. The live venue is{" "}
              <Link href="/ftmo" className="underline hover:text-foreground">
                FTMO
              </Link>
              . To re-enable these screens, set{" "}
              <code>ibkr.web_enabled</code> true in{" "}
              <code>trader_settings.json</code>.
            </div>
          )}

          {connState === "live" && !paper && (
            <div className="px-4 py-1.5 text-xs border-t bg-loss/15 border-loss/40 text-loss font-medium">
              Account {conn?.account} is NOT a verified paper account — every
              trading control is disabled.
            </div>
          )}
        </header>

        <main className="flex-1 min-h-0">{children}</main>
      </div>
    </ShellCtx.Provider>
  );
}

function AccountBadge({
  account,
  paper,
  connState,
}: {
  account: string | null;
  paper: boolean;
  connState: "live" | "degraded" | "offline";
}) {
  if (connState === "offline" || !account) return null;
  return (
    <Badge
      variant="outline"
      className={cn(
        "font-mono text-[11px] gap-1.5",
        paper
          ? "border-profit/40 text-profit"
          : "border-loss/50 text-loss font-bold"
      )}
      title={
        paper
          ? "Paper account, verified by account id (not by port number)."
          : "NOT a verified paper account."
      }
    >
      {paper ? "PAPER" : "NOT PAPER"}
      <span className="text-muted-foreground">{account}</span>
    </Badge>
  );
}

/**
 * The FTMO account and its live equity.
 *
 * Equity, not balance, and that is the point: every FTMO limit is measured on
 * equity INCLUDING floating P&L, so the account can fail a limit with no order
 * placed. Showing balance in the header would show the number that cannot
 * breach anything.
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
    <Badge
      variant="outline"
      className="border-primary/40 font-mono text-[11px] gap-1.5"
      title="FTMO Challenge account. Simulated capital — every limit is measured on equity including floating P&L."
    >
      FTMO
      <span className="text-muted-foreground">
        {equity === null
          ? DASH
          : equity.toLocaleString(undefined, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}
      </span>
    </Badge>
  );
}

function ConnectionPill({
  state,
  detail,
  label: venue,
}: {
  state: "live" | "degraded" | "offline";
  detail: string;
  /** Which venue this pill is about. Two venues are shown at once now, so an
   *  unlabelled "Degraded" would not say degraded at what. */
  label?: string;
}) {
  const stateLabel = { live: "Live", degraded: "Degraded", offline: "Offline" }[
    state
  ];
  const label = venue ? `${venue} ${stateLabel}` : stateLabel;
  const dot = {
    live: "bg-profit",
    degraded: "bg-unknown",
    offline: "bg-loss",
  }[state];
  return (
    <span
      className="hidden lg:flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground"
      title={detail}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          dot,
          state === "live" && "animate-pulse"
        )}
      />
      {label}
    </span>
  );
}

export { Power };
