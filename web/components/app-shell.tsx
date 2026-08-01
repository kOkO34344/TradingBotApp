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
 * The connection pill distinguishes three states on purpose:
 *   live      — socket open AND Gateway connected
 *   degraded  — backend reachable, Gateway is not (data will be stale/absent)
 *   offline   — backend itself unreachable (the API isn't running)
 * Collapsing these into "disconnected" would hide which thing to go fix.
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
  Wallet,
} from "lucide-react";

import { api, type Status } from "@/lib/api";
import { useLive } from "@/lib/use-live";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { KillSwitch } from "@/components/kill-switch";

const NAV = [
  { href: "/charts", label: "Charts", icon: BarChart3 },
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/positions", label: "Positions", icon: Wallet },
  { href: "/rebalance", label: "Rebalance", icon: Repeat },
  { href: "/journal", label: "Journal", icon: BookOpen },
  { href: "/kronos", label: "Kronos", icon: Brain },
  { href: "/backtests", label: "Backtests", icon: Activity },
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
            <Link href="/charts" className="flex items-center gap-2 shrink-0">
              <span className="grid size-7 place-items-center rounded bg-primary text-primary-foreground font-bold text-sm">
                T
              </span>
              <span className="font-semibold tracking-tight hidden sm:inline">
                TradingBot
              </span>
            </Link>

            <nav className="flex items-center gap-0.5 overflow-x-auto">
              {NAV.map(({ href, label, icon: Icon }) => {
                const active =
                  pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    className={cn(
                      "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm transition-colors whitespace-nowrap",
                      active
                        ? "bg-accent text-accent-foreground font-medium"
                        : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                    )}
                  >
                    <Icon className="size-4" />
                    <span className="hidden md:inline">{label}</span>
                  </Link>
                );
              })}
            </nav>

            <div className="ml-auto flex items-center gap-2">
              <AccountBadge
                account={conn?.account ?? null}
                paper={paper}
                connState={connState}
              />
              <ConnectionPill
                state={connState}
                detail={
                  connState === "live"
                    ? `${conn?.host}:${conn?.port} · clientId ${conn?.clientId}`
                    : (conn?.error ?? statusError ?? "")
                }
              />
              <KillSwitch
                autotradeEnabled={status?.autotrade.enabled ?? false}
                disabled={!tradingAllowed}
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

          {connState !== "live" && (
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

function ConnectionPill({
  state,
  detail,
}: {
  state: "live" | "degraded" | "offline";
  detail: string;
}) {
  const label = { live: "Live", degraded: "Degraded", offline: "Offline" }[
    state
  ];
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
