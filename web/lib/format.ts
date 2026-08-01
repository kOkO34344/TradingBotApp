/**
 * format.ts — number and time formatting for the trading UI.
 *
 * One rule runs through all of it: a value that isn't known renders as a
 * dash, never as zero. `$0.00` and "unknown" look identical at a glance and
 * mean opposite things on a P&L line, and this project has already been
 * burned twice by a display that implied certainty it didn't have.
 */

export const DASH = "—";

export function fmtNumber(
  value: number | null | undefined,
  decimals = 2
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return value.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function fmtUsd(
  value: number | null | undefined,
  decimals = 2
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return `$${fmtNumber(value, decimals)}`;
}

/** Signed, for P&L: the sign is the point, so it's always shown. */
export function fmtSigned(
  value: number | null | undefined,
  decimals = 2,
  prefix = "$"
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${prefix}${fmtNumber(Math.abs(value), decimals)}`;
}

export function fmtPct(
  value: number | null | undefined,
  decimals = 2,
  signed = true
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const sign = signed ? (value > 0 ? "+" : value < 0 ? "−" : "") : "";
  return `${sign}${fmtNumber(Math.abs(value), decimals)}%`;
}

/** Price precision by asset class — 1.15246 for FX, 307.36 for a stock. */
export function fmtPrice(
  value: number | null | undefined,
  kind = "stock"
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  if (kind === "forex") return fmtNumber(value, 5);
  if (kind === "crypto") return fmtNumber(value, value < 10 ? 4 : 2);
  return fmtNumber(value, 2);
}

export function fmtQty(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return Number.isInteger(value) ? String(value) : fmtNumber(value, 4);
}

/** "3m ago" / "2h ago" — for data age, where the unit matters more than precision. */
export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds))
    return DASH;
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function fmtTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return DASH;
  return new Date(epochSeconds * 1000).toLocaleString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtDate(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return DASH;
  return new Date(epochSeconds * 1000).toLocaleDateString("en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

/** Tailwind text colour for a P&L value. Zero is neutral, not green. */
export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value))
    return "text-muted-foreground";
  if (value > 0) return "text-profit";
  if (value < 0) return "text-loss";
  return "text-muted-foreground";
}
