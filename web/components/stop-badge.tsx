"use client";

/**
 * stop-badge.tsx — the three-state verdict on whether a position is covered.
 *
 * Three states, never two. `protected === null` means IBKR did not answer the
 * open-orders request, which is missing information, not a negative answer.
 * Rendering that as "UNPROTECTED" would manufacture an emergency: on
 * 2026-07-29 a wedged Gateway on this machine answered position requests
 * normally while reqAllOpenOrders timed out, and every position would have
 * lit up red while their GTC stops sat safely at IBKR the whole time.
 *
 * "Protected" also means GTC specifically, not "a stop exists". A DAY stop
 * looks like protection and silently stops being protection at the close —
 * that is the 2026-07-21 incident, three positions naked overnight.
 */

import { AlertTriangle, Check, HelpCircle } from "lucide-react";

import type { Position } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function StopBadge({
  position,
  size = "default",
}: {
  position: Position;
  size?: "default" | "sm";
}) {
  const cls = size === "sm" ? "text-[10px] px-1.5 py-0" : "";

  if (position.protected === null) {
    return (
      <Badge
        variant="outline"
        className={cn("gap-1 border-unknown/50 text-unknown", cls)}
        title={position.protectionReason}
      >
        <HelpCircle className="size-3" />
        UNKNOWN
      </Badge>
    );
  }

  if (!position.protected) {
    return (
      <Badge
        variant="destructive"
        className={cn("gap-1", cls)}
        title={position.protectionReason}
      >
        <AlertTriangle className="size-3" />
        UNPROTECTED
      </Badge>
    );
  }

  return (
    <Badge
      variant="outline"
      className={cn("gap-1 border-profit/40 text-profit", cls)}
      title={position.protectionReason}
    >
      <Check className="size-3" />
      GTC
    </Badge>
  );
}
