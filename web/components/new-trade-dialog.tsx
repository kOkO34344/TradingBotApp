"use client";

/**
 * new-trade-dialog.tsx — manual entry, always as a bracket.
 *
 * There is no "market order" option here and there will not be one. Rule 2:
 * no order without a stop. `place_bracket_order` is the entry path, and a
 * bare market order requires `allow_no_stop=True` set deliberately in code —
 * not a checkbox in a browser.
 *
 * Quantity and stop both default to blank, meaning "size it the way
 * paper_trader would": risk budget divided by a 2xATR stop distance, clamped
 * to the notional cap using the buffered entry price. Typing a number
 * overrides that, and the preview says which of the two produced the order
 * so an override is never invisible.
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";

import { trade, type TradePreview } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { TradeActionDialog } from "@/components/trade-action";

export function NewTradeDialog({
  open,
  onOpenChange,
  onDone,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDone?: () => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [action, setAction] = useState<"BUY" | "SELL">("BUY");
  const [quantity, setQuantity] = useState("");
  const [stopPrice, setStopPrice] = useState("");
  const [preparing, setPreparing] = useState(false);
  const [loader, setLoader] = useState<(() => Promise<TradePreview>) | null>(
    null
  );
  const [previewOpen, setPreviewOpen] = useState(false);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    if (!sym) return;
    const qty = quantity.trim() ? Number(quantity) : null;
    const stop = stopPrice.trim() ? Number(stopPrice) : null;
    setPreparing(true);
    setLoader(() => () =>
      trade
        .previewBracket({
          symbol: sym,
          action,
          quantity: qty,
          stopPrice: stop,
        })
        .finally(() => setPreparing(false))
    );
    setPreviewOpen(true);
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New position</DialogTitle>
            <DialogDescription>
              Placed as a bracket: limit entry with an attached GTC stop. There
              is no un-stopped entry path in this UI.
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="nt-symbol">Symbol</Label>
              <Input
                id="nt-symbol"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="AAPL"
                className="font-mono uppercase"
                autoFocus
                spellCheck={false}
              />
              <p className="text-xs text-muted-foreground">
                US stocks only — the order path sizes off daily ATR and places
                a stock bracket.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label>Side</Label>
              <div className="flex rounded-md border border-border p-0.5">
                {(["BUY", "SELL"] as const).map((side) => (
                  <button
                    key={side}
                    type="button"
                    onClick={() => setAction(side)}
                    className={cn(
                      "flex-1 rounded px-3 py-1.5 text-sm font-medium transition-colors",
                      action === side
                        ? side === "BUY"
                          ? "bg-profit/15 text-profit"
                          : "bg-loss/15 text-loss"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {side}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="nt-qty">Quantity</Label>
                <Input
                  id="nt-qty"
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  placeholder="auto"
                  inputMode="numeric"
                  className="tabular-nums"
                />
                <p className="text-xs text-muted-foreground">
                  Blank = risk budget / 2×ATR
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="nt-stop">Stop price</Label>
                <Input
                  id="nt-stop"
                  value={stopPrice}
                  onChange={(e) => setStopPrice(e.target.value)}
                  placeholder="auto"
                  inputMode="decimal"
                  className="tabular-nums"
                />
                <p className="text-xs text-muted-foreground">
                  Blank = 2×ATR from price
                </p>
              </div>
            </div>

            <DialogFooter>
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!symbol.trim() || preparing}>
                {preparing && (
                  <Loader2 className="mr-1.5 size-4 animate-spin" />
                )}
                Preview order
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <TradeActionDialog
        open={previewOpen}
        onOpenChange={(v) => {
          setPreviewOpen(v);
          if (!v) setPreparing(false);
        }}
        loadPreview={loader}
        onDone={() => {
          onOpenChange(false);
          setSymbol("");
          setQuantity("");
          setStopPrice("");
          onDone?.();
        }}
      />
    </>
  );
}
