"use client";

/**
 * kill-switch.tsx — always-visible cut-out for the unattended FTMO runner.
 *
 * Reachable from every screen without navigating, because the moment you want
 * it is the moment you don't want to hunt for it.
 *
 * Disarming is treated as always-safe and needs no confirmation: the worst
 * case is the experiment pauses. Arming is the risky direction and gets a
 * dialog spelling out what will actually happen — CLAUDE.md rule 9 is explicit
 * that this runs against the project's own evidence, so the UI states that at
 * the moment of arming rather than presenting it as a neutral switch.
 *
 * NOT GATED ON THE VENUE BEING REACHABLE. A kill switch that needs a healthy
 * connection is not a kill switch. It writes `trader_settings.json`, which the
 * runner re-reads on every wakeup, so it works whether or not cTrader is up.
 *
 * `armed === null` means we could not read the toggle, and it renders as its
 * own state rather than as "off". Telling Koko the robot is disarmed when we
 * do not know is the same error class as rendering an unknown stop as an
 * absent one.
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
  armed,
  onChanged,
}: {
  /** `null` while unknown — never coerce it to false. */
  armed: boolean | null;
  onChanged: () => void;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const setEnabled = async (enabled: boolean) => {
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/ftmo/autotrade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body?.detail ?? `${res.status}`);
      toast[enabled ? "warning" : "success"](
        enabled ? "FTMO runner ARMED" : "FTMO runner disarmed",
        {
          description: enabled
            ? "Kronos will trade unattended from the next wakeup. The rule engine, sizer and server-side stop stay enforced."
            : "The runner will wake and place nothing. The launchd job stays installed.",
        }
      );
      onChanged();
    } catch (err) {
      toast.error("Could not change the arm state", {
        description: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  if (armed === true) {
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
        <span className="hidden sm:inline">Disarm</span>
      </Button>
    );
  }

  // Unknown is offered as an arm button too, and says so. Refusing to act
  // would leave no way to reach the switch when the read is what is broken.
  const unknown = armed === null;

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        onClick={() => setConfirmOpen(true)}
        title={
          unknown
            ? "Couldn't read the arm toggle — whether the runner fires is unknown"
            : "Arm unattended trading"
        }
        className={cn(
          "gap-1.5",
          unknown ? "border-unknown/50 text-unknown" : "text-muted-foreground"
        )}
      >
        <Power className="size-4" />
        <span className="hidden sm:inline">
          {unknown ? "Arm state unknown" : "Disarmed"}
        </span>
      </Button>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <TriangleAlert className="size-5 text-unknown" />
              Arm unattended trading?
            </DialogTitle>
            <DialogDescription>
              Kronos will place real orders on the FTMO Challenge account with
              no approval prompt, every 15 minutes between 16:30 and 23:00
              Sofia time, Monday to Friday.
            </DialogDescription>
          </DialogHeader>

          <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
            <li>
              The rule engine, the per-trade and portfolio risk caps and the
              server-side stop attached at entry all stay enforced. Autonomy
              removes the approval step, never a limit.
            </li>
            <li>
              All four asset classes <span className="font-medium">failed</span>{" "}
              their IC screen twice — at a 20-day horizon on 2026-08-03 and
              again at 5 days on 2026-08-08, with no |t| above 1.55. Running
              anyway is a deliberate, recorded override: the third exception to
              the evidence rule, not a validated strategy.
            </li>
            <li>
              Capital is simulated, so this does not breach rule 1. The real
              exposure is the entry fee.
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
              Arm the runner
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
