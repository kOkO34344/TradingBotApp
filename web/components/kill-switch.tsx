"use client";

/**
 * kill-switch.tsx — always-visible autotrade cut-out.
 *
 * Reachable from every screen without navigating, because the moment you
 * want it is the moment you don't want to hunt for it.
 *
 * Turning autotrade OFF is treated as always-safe and needs no confirmation:
 * the worst case is the hourly experiment pauses. Turning it ON is the risky
 * direction and gets a confirmation dialog spelling out what will actually
 * happen — CLAUDE.md rule 7 is explicit that this feature runs against the
 * project's own evidence, so the UI states that at the moment of arming it
 * rather than presenting it as a neutral switch.
 *
 * The button stays functional while IB Gateway is down. A kill switch that
 * needs a healthy broker connection is not a kill switch.
 */

import { useState } from "react";
import { Power, PowerOff, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { API_BASE } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function KillSwitch({
  autotradeEnabled,
  disabled,
  disabledReason,
  onChanged,
  venue = "ibkr",
}: {
  autotradeEnabled: boolean;
  disabled: boolean;
  disabledReason: string;
  onChanged: () => void;
  /**
   * Which venue's unattended runner this switch cuts.
   *
   * It follows the venue that can actually trade. Once IBKR's web connection
   * went off by default, the header switch was cutting a runner on a retired
   * broker while the FTMO runner fired unattended every hour — a kill switch
   * pointed at the wrong venue is worse than none, because it looks like
   * cover it does not provide.
   */
  venue?: "ibkr" | "ftmo";
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const isFtmo = venue === "ftmo";
  const endpoint = isFtmo ? "/api/ftmo/autotrade" : "/api/autotrade";

  const setEnabled = async (enabled: boolean) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `${res.status}`);
      toast[enabled ? "warning" : "success"](
        enabled
          ? `${isFtmo ? "FTMO" : "IBKR"} autotrade ARMED`
          : `${isFtmo ? "FTMO" : "IBKR"} autotrade disabled`,
        {
          description: enabled
            ? isFtmo
              ? "Kronos will trade FTMO unattended from the next firing. The rule engine, sizer and server-side stop stay enforced."
              : "The hourly runner will trade unattended from the next firing. RiskGuard stays enforced."
            : "The runner will place nothing. The launchd job stays installed.",
        }
      );
      onChanged();
    } catch (err) {
      toast.error("Could not change autotrade", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  if (autotradeEnabled) {
    return (
      <Button
        size="sm"
        variant="destructive"
        disabled={busy}
        onClick={() => setEnabled(false)}
        className="gap-1.5 font-medium"
        title="Stop unattended trading immediately"
      >
        <PowerOff className="size-4" />
        <span className="hidden sm:inline">Stop autotrade</span>
      </Button>
    );
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={busy || disabled}
        onClick={() => setConfirmOpen(true)}
        title={disabled ? disabledReason : "Arm unattended hourly trading"}
        className={cn("gap-1.5 text-muted-foreground", disabled && "opacity-50")}
      >
        <Power className="size-4" />
        <span className="hidden sm:inline">Autotrade off</span>
      </Button>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <TriangleAlert className="size-5 text-unknown" />
              Arm unattended trading?
            </DialogTitle>
            <DialogDescription>
              {isFtmo
                ? "Kronos will place real orders on the FTMO Challenge account with no approval prompt, hourly between 16:30 and 11:30 Sofia time, every day except Sunday."
                : "The hourly runner will place real paper orders with no approval prompt, every hour the NYSE is open."}
            </DialogDescription>
          </DialogHeader>

          <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
            {isFtmo ? (
              <>
                <li>
                  The rule engine, the per-trade and portfolio risk caps and the
                  server-side stop attached at entry all stay enforced. Autonomy
                  removes the approval step, never a limit.
                </li>
                <li>
                  All four asset classes <span className="font-medium">failed</span>{" "}
                  their IC screen in 2026-08-03 (no |t| above 1.55). Running
                  anyway is a deliberate, recorded override — the third
                  exception to the evidence rule, not a validated strategy.
                </li>
                <li>
                  Capital is simulated, so this does not breach rule 1. The real
                  exposure is the entry fee.
                </li>
              </>
            ) : (
              <>
                <li>
                  RiskGuard stays fully enforced — order notional, max positions,
                  daily-loss breaker, stop required.
                </li>
                <li>
                  The signal is <span className="font-mono">kronos</span>.
                  Momentum is disabled in code; if it were selected the runner
                  would refuse to fire rather than substitute another signal.
                </li>
                <li>
                  Neither eligible signal showed measurable edge at hourly
                  cadence (IC −0.081 / −0.037). This is a live experiment, not a
                  validated strategy.
                </li>
              </>
            )}
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
              Arm autotrade
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
