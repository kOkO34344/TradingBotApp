"use client";

/**
 * trade-action.tsx — the preview-before-commit dialog every write goes through.
 *
 * The flow is fixed and there is no way around it:
 *   click -> backend builds the exact order + RiskGuard verdict -> the dialog
 *   shows it -> confirm sends only the preview token.
 *
 * Things this component does deliberately:
 *
 *  - Shows the ORDER, not a summary of intent: side, quantity, both prices,
 *    and the TIF of each leg. The TIF is displayed because an unset/DAY stop
 *    is the specific bug that left three positions naked overnight.
 *  - Shows warnings above the confirm button, not below it. When resting
 *    orders can't be enumerated, flattening can leave a stale stop that later
 *    fires against nothing and opens a reversed position — that has to be
 *    read before clicking.
 *  - Counts the preview down. A quote goes stale; at zero the confirm button
 *    disables and you re-preview rather than sending a price that moved.
 *  - Never disables the confirm button on a RiskGuard block. It shows the
 *    block and offers no confirm at all — there is nothing to override here,
 *    and a greyed button invites hunting for the override.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Ban, Loader2, ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { ApiError, trade, type TradePreview } from "@/lib/api";
import { DASH, fmtPct, fmtPrice, fmtQty, fmtUsd } from "@/lib/format";
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

const TITLES: Record<TradePreview["kind"], string> = {
  flatten: "Flatten position",
  reprotect: "Place GTC stop",
  bracket: "New bracket order",
  cancel: "Cancel order",
};

export function TradeActionDialog({
  open,
  onOpenChange,
  loadPreview,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called when the dialog opens; returns the preview to display. */
  loadPreview: (() => Promise<TradePreview>) | null;
  onDone?: () => void;
}) {
  const [preview, setPreview] = useState<TradePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);

  const reset = useCallback(() => {
    setPreview(null);
    setError(null);
    setSecondsLeft(0);
  }, []);

  useEffect(() => {
    if (!open || !loadPreview) return;
    // Opening the dialog is what triggers the preview request, so clearing
    // the previous one and marking this in flight is the effect's purpose,
    // not an accidental cascade — it runs once per open.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reset();
    setLoading(true);
    loadPreview()
      .then((p) => {
        setPreview(p);
        setSecondsLeft(p.expiresInSeconds);
      })
      .catch((err: unknown) =>
        setError(err instanceof ApiError ? err.message : String(err))
      )
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!preview || secondsLeft <= 0) return;
    const t = setTimeout(() => setSecondsLeft((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [preview, secondsLeft]);

  const confirm = async () => {
    if (!preview) return;
    setSubmitting(true);
    try {
      const result = await trade.execute(preview.kind, preview.token);
      toast.success(`${TITLES[preview.kind]} sent`, {
        description: `${preview.symbol} — status ${String(
          result.status ?? "submitted"
        )}. Check the journal and positions to confirm.`,
      });
      onOpenChange(false);
      onDone?.();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      // A 504 here means the outcome is genuinely unknown — the order may
      // have reached IBKR. Say that rather than implying it failed.
      const unknown = err instanceof ApiError && err.status === 504;
      toast.error(unknown ? "Outcome UNKNOWN" : "Action failed", {
        description: message,
        duration: unknown ? 30000 : 8000,
      });
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const expired = preview !== null && secondsLeft <= 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {preview ? TITLES[preview.kind] : "Preview"}
            {preview?.symbol ? (
              <span className="ml-2 font-mono text-muted-foreground">
                {preview.symbol}
              </span>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            Nothing is sent to IBKR until you confirm the exact order below.
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Building the order and checking RiskGuard…
          </div>
        )}

        {error && !loading && (
          <div className="rounded-md border border-loss/40 bg-loss/10 p-3 text-sm">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-loss" />
              <p className="text-loss">{error}</p>
            </div>
          </div>
        )}

        {preview && !loading && (
          <div className="space-y-3">
            <OrderSummary preview={preview} />

            {!preview.allowed && (
              <div className="rounded-md border border-loss/50 bg-loss/10 p-3 text-sm">
                <div className="flex items-start gap-2">
                  <Ban className="mt-0.5 size-4 shrink-0 text-loss" />
                  <div>
                    <p className="font-medium text-loss">
                      RiskGuard blocked this order
                    </p>
                    <p className="mt-0.5 text-muted-foreground">
                      {preview.reason}
                    </p>
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      Limits live in <code>risk_limits.json</code> and changing
                      them is a deliberate edit, not something this dialog can
                      override.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {(preview.warnings ?? []).map((w, i) => (
              <div
                key={i}
                className="rounded-md border border-unknown/50 bg-unknown/10 p-3 text-sm"
              >
                <div className="flex items-start gap-2">
                  <ShieldAlert className="mt-0.5 size-4 shrink-0 text-unknown" />
                  <p className="text-muted-foreground">{w}</p>
                </div>
              </div>
            ))}

            <div>
              <p className="mb-1 text-xs uppercase tracking-wide text-muted-foreground">
                What will happen
              </p>
              <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                {preview.steps.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ol>
            </div>

            {preview.allowed && (
              <p
                className={cn(
                  "text-xs",
                  expired ? "text-loss" : "text-muted-foreground"
                )}
              >
                {expired
                  ? "This preview expired — prices have moved. Close and preview again."
                  : `Quoted prices valid for ${secondsLeft}s.`}
              </p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {preview?.allowed ? "Cancel" : "Close"}
          </Button>
          {preview?.allowed && (
            <Button
              variant={preview.kind === "flatten" ? "destructive" : "default"}
              disabled={submitting || expired}
              onClick={confirm}
            >
              {submitting && <Loader2 className="mr-1.5 size-4 animate-spin" />}
              Send to IBKR
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OrderSummary({ preview }: { preview: TradePreview }) {
  const rows: [string, React.ReactNode][] = [];

  if (preview.action) {
    rows.push([
      "Order",
      <span key="o" className="font-mono">
        <span
          className={
            preview.action === "BUY" ? "text-profit" : "text-loss"
          }
        >
          {preview.action}
        </span>{" "}
        {fmtQty(preview.quantity)} {preview.symbol}
      </span>,
    ]);
  }

  if (preview.kind === "flatten") {
    rows.push(["Type", <span key="t" className="font-mono">MARKET</span>]);
    rows.push(["Est. price", fmtPrice(preview.estimatedPrice)]);
    rows.push(["Est. proceeds", fmtUsd(preview.estimatedProceeds)]);
    rows.push([
      "Orders to cancel",
      preview.ordersUnknown ? (
        <span key="c" className="text-unknown">
          unknown
        </span>
      ) : (
        String(preview.ordersToCancel?.length ?? 0)
      ),
    ]);
  }

  if (preview.kind === "reprotect") {
    rows.push(["Type", <span key="t" className="font-mono">STOP</span>]);
    rows.push([
      "Stop price",
      <span key="s" className="font-mono">
        {fmtPrice(preview.stopPrice)}
      </span>,
    ]);
    rows.push(["Current price", fmtPrice(preview.currentPrice)]);
    rows.push([
      "Distance",
      preview.distancePct !== undefined
        ? fmtPct(preview.distancePct, 2, false)
        : DASH,
    ]);
    rows.push(["Risk if hit", fmtUsd(preview.riskIfHit)]);
    rows.push(["TIF", <TifBadge key="tif" tif={preview.tif ?? "GTC"} />]);
  }

  if (preview.kind === "bracket") {
    rows.push([
      "Entry limit",
      <span key="e" className="font-mono">
        {fmtPrice(preview.entryLimit)}{" "}
        <TifBadge tif={preview.parentTif ?? "DAY"} isStop={false} />
      </span>,
    ]);
    rows.push([
      "Stop",
      <span key="s" className="font-mono">
        {fmtPrice(preview.stopPrice)} <TifBadge tif={preview.stopTif ?? "GTC"} />
      </span>,
    ]);
    rows.push(["Stop from", preview.stopSource ?? DASH]);
    rows.push(["Size from", preview.quantitySource ?? DASH]);
    rows.push(["Notional", fmtUsd(preview.notional, 0)]);
    rows.push([
      "Risk if stopped",
      <span key="r">
        {fmtUsd(preview.riskIfStopped)}{" "}
        <span className="text-muted-foreground">
          ({fmtPct(preview.riskPctOfEquity, 2, false)} of equity)
        </span>
      </span>,
    ]);
  }

  if (preview.kind === "cancel") {
    rows.push(["Order id", String(preview.orderId)]);
    rows.push(["Type", <span key="t" className="font-mono">{preview.orderType}</span>]);
    rows.push(["TIF", <TifBadge key="tif" tif={preview.tif ?? ""} />]);
  }

  return (
    <div className="rounded-md border border-border">
      <dl className="divide-y divide-border/60 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between gap-4 px-3 py-2">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="text-right tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

/** GTC is what keeps a stop alive overnight; anything else is called out. */
function TifBadge({ tif, isStop = true }: { tif: string; isStop?: boolean }) {
  const bad = isStop && tif !== "GTC";
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px]",
        bad ? "border-loss/50 text-loss" : "text-muted-foreground"
      )}
      title={
        bad
          ? "A stop with a non-GTC TIF expires at the session close and stops protecting the position."
          : undefined
      }
    >
      {tif}
    </Badge>
  );
}
