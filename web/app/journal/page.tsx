"use client";

/**
 * journal/page.tsx — trade_journal.csv, with its corrections made visible.
 *
 * The journal is append-only and has been wrong twice in ways that mattered,
 * so this screen's job is not just to display rows — it is to show where the
 * record and reality disagreed:
 *
 *   - Rows a later RESULT_CORRECTED overturned are struck through and
 *     labelled. Two orders that actually FILLED were journalled `Cancelled`
 *     a second after placement, and the account ran two positions ahead of
 *     every record for a day.
 *   - Rows a later NOTE disowns ("phantom", "fabricated") are marked
 *     disputed, with the note text on hover. Two CLOSE_DETECTED rows claimed
 *     positions had liquidated when they were open the whole time.
 *
 * Nothing is hidden. Deleting the wrong rows would destroy the audit trail;
 * the contradiction IS the information, so both versions stay on screen.
 */

import { useMemo, useState } from "react";
import { AlertTriangle, Ban, Filter } from "lucide-react";

import { api, type JournalRow } from "@/lib/api";
import { useFetch, useLive } from "@/lib/use-live";
import { DASH, fmtPrice, fmtQty } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

const EVENT_STYLES: Record<string, string> = {
  SUBMIT: "text-muted-foreground",
  RESULT: "text-foreground",
  RESULT_CORRECTED: "text-primary font-medium",
  BLOCKED: "text-loss",
  UNPROTECTED: "text-loss font-medium",
  CLOSE_FILLED: "text-unknown",
  CLOSE_DETECTED: "text-unknown",
  CLOSE_RECONSTRUCTED: "text-unknown",
  NOTE: "text-muted-foreground italic",
};

export default function JournalPage() {
  const live = useLive();
  const journal = useFetch(
    () => api.journal({ limit: 2000 }),
    [live.revisions.fills, live.revisions.orders]
  );
  const [query, setQuery] = useState("");
  const [eventFilter, setEventFilter] = useState<string | null>(null);
  const [hideNotes, setHideNotes] = useState(false);

  const rows = journal.data?.rows ?? [];
  const summary = journal.data?.summary;

  const eventCounts = summary?.byEvent ?? {};

  const filtered = useMemo(() => {
    const q = query.trim().toUpperCase();
    return rows.filter((r) => {
      if (eventFilter && r.event !== eventFilter) return false;
      if (hideNotes && r.event === "NOTE") return false;
      if (!q) return true;
      return (
        r.symbol.toUpperCase().includes(q) ||
        r.event.toUpperCase().includes(q) ||
        r.detail.toUpperCase().includes(q) ||
        r.status.toUpperCase().includes(q)
      );
    });
  }, [rows, query, eventFilter, hideNotes]);

  return (
    <div className="mx-auto max-w-7xl space-y-4 p-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Trade journal</h1>
        <p className="text-sm text-muted-foreground">
          Every order attempt, block and fill. Corrections are shown alongside
          what they corrected — nothing is removed.
        </p>
      </div>

      {(summary?.superseded ?? 0) > 0 || (summary?.disputed ?? 0) > 0 ? (
        <Card className="border-primary/30 bg-primary/5 p-3 text-sm">
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-primary" />
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">
                {summary?.superseded ?? 0} row(s) superseded
              </span>{" "}
              by a later correction and{" "}
              <span className="font-medium text-foreground">
                {summary?.disputed ?? 0} disputed
              </span>{" "}
              as phantom. For anything before 2026-07-28, trust the correction
              over the original — a <code>Cancelled</code> written one second
              after placement was a snapshot, not an outcome.
            </p>
          </div>
        </Card>
      ) : null}

      {/* -------------------------------------------------------- filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <Filter className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by symbol, status or detail"
            className="w-72 pl-8"
          />
        </div>

        <Button
          variant={eventFilter === null ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setEventFilter(null)}
        >
          All ({rows.length})
        </Button>
        {Object.entries(eventCounts)
          .sort((a, b) => b[1] - a[1])
          .map(([event, count]) => (
            <Button
              key={event}
              variant={eventFilter === event ? "secondary" : "ghost"}
              size="sm"
              onClick={() =>
                setEventFilter(eventFilter === event ? null : event)
              }
              className={cn("font-mono text-xs", EVENT_STYLES[event])}
            >
              {event} ({count})
            </Button>
          ))}
        <Button
          variant={hideNotes ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setHideNotes((v) => !v)}
          className="ml-auto text-xs"
        >
          {hideNotes ? "Show notes" : "Hide notes"}
        </Button>
      </div>

      {/* ---------------------------------------------------------- table */}
      <Card className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-3 py-2.5 text-left font-medium">Time</th>
                <th className="px-3 py-2.5 text-left font-medium">Event</th>
                <th className="px-3 py-2.5 text-left font-medium">Symbol</th>
                <th className="px-3 py-2.5 text-left font-medium">Side</th>
                <th className="px-3 py-2.5 text-right font-medium">Qty</th>
                <th className="px-3 py-2.5 text-right font-medium">Price</th>
                <th className="px-3 py-2.5 text-right font-medium">Stop</th>
                <th className="px-3 py-2.5 text-left font-medium">Status</th>
                <th className="px-3 py-2.5 text-left font-medium">Detail</th>
              </tr>
            </thead>
            <tbody>
              {journal.loading && rows.length === 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="px-3 py-10 text-center text-muted-foreground"
                  >
                    Reading trade_journal.csv…
                  </td>
                </tr>
              )}
              {filtered.map((row) => (
                <JournalTableRow key={row.index} row={row} />
              ))}
              {!journal.loading && filtered.length === 0 && (
                <tr>
                  <td
                    colSpan={9}
                    className="px-3 py-10 text-center text-muted-foreground"
                  >
                    No rows match this filter.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-xs text-muted-foreground">
        Source: <code>{summary?.path}</code> · {summary?.total ?? 0} rows
      </p>
    </div>
  );
}

function JournalTableRow({ row }: { row: JournalRow }) {
  const invalid = row.superseded || row.disputed;
  return (
    <tr
      className={cn(
        "border-b border-border/50 last:border-0 align-top",
        invalid ? "opacity-55" : "hover:bg-accent/25"
      )}
    >
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted-foreground">
        {row.timestamp.replace("T", " ")}
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-col gap-1">
          <span
            className={cn(
              "font-mono text-xs",
              EVENT_STYLES[row.event] ?? "text-foreground"
            )}
          >
            {row.event}
          </span>
          {row.superseded && (
            <Badge
              variant="outline"
              className="w-fit gap-1 border-primary/40 text-[10px] text-primary"
              title={`Overturned by row #${row.supersededBy}`}
            >
              <Ban className="size-2.5" />
              SUPERSEDED
            </Badge>
          )}
          {row.disputed && (
            <Badge
              variant="outline"
              className="w-fit gap-1 border-unknown/50 text-[10px] text-unknown"
              title={row.disputeNote}
            >
              <AlertTriangle className="size-2.5" />
              PHANTOM
            </Badge>
          )}
        </div>
      </td>
      <td
        className={cn(
          "px-3 py-2 font-mono font-medium",
          invalid && "line-through"
        )}
      >
        {row.symbol || DASH}
      </td>
      <td
        className={cn(
          "px-3 py-2",
          row.action === "BUY"
            ? "text-profit"
            : row.action === "SELL"
              ? "text-loss"
              : "text-muted-foreground"
        )}
      >
        {row.action || DASH}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {fmtQty(row.quantity)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {fmtPrice(row.price)}
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-muted-foreground">
        {fmtPrice(row.stop)}
      </td>
      <td className="px-3 py-2 text-xs">{row.status || DASH}</td>
      <td className="max-w-md px-3 py-2 text-xs text-muted-foreground">
        <span className="line-clamp-3" title={row.detail}>
          {row.detail}
        </span>
        {row.disputed && row.disputeNote && (
          <span className="mt-1 block text-unknown line-clamp-2" title={row.disputeNote}>
            Disputed: {row.disputeNote}
          </span>
        )}
      </td>
    </tr>
  );
}
