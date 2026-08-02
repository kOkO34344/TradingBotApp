"use client";

/**
 * symbol-search.tsx — typeahead for the chart's symbol box.
 *
 * Deliberately not a shadcn Combobox: this input must stay freely typeable.
 * The box accepts forms no search index knows about — `FX:EURUSD`,
 * `FUT:ES:202612:CME`, `BRK.B` — and a component that only accepts a
 * selection would take that away. So it is a plain input with suggestions
 * layered on: type anything and press Enter, or pick a row.
 *
 * Each row carries the exact query string the chart will receive (built by
 * the API), and the row shows what that resolves to — venue, currency, and
 * the pair for FX. A foreign listing therefore cannot be picked by accident
 * thinking it is the US one, which matters because `STK:NVDA:MXN` and
 * `NVDA` are different instruments that look identical in a bare list.
 *
 * Results are debounced, and a request that returns after a newer one is
 * discarded — otherwise a slow "N" response can overwrite the "NVDA" list.
 */

import { useEffect, useRef, useState } from "react";
import { Loader2, Search, Star, TriangleAlert } from "lucide-react";

import { api, type SymbolSuggestion } from "@/lib/api";
import { cn } from "@/lib/utils";

const DEBOUNCE_MS = 180;
const MIN_CHARS = 1;

export function SymbolSearch({
  value,
  onChange,
  onSubmit,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  /** Called with the query string to chart. */
  onSubmit: (query: string) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<SymbolSuggestion[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const requestId = useRef(0);
  const suppress = useRef(false);

  const query = value.trim();
  // Derived, not stored: an empty box simply hides the list. Clearing state
  // from inside the effect would be a synchronous setState during render
  // commit, which cascades renders (and eslint's react-hooks rule flags it).
  const canSearch = query.length >= MIN_CHARS;

  useEffect(() => {
    if (suppress.current) {
      // Just picked a row — don't immediately re-search its own text.
      suppress.current = false;
      return;
    }
    if (!canSearch) return;

    const id = ++requestId.current;
    const timer = setTimeout(() => {
      setLoading(true);
      api
        .searchSymbols(query)
        .then((res) => {
          // A stale response must never replace a newer one.
          if (id !== requestId.current) return;
          setResults(res.results);
          setNote(res.note);
          setActive(0);
          setOpen(true);
        })
        .catch(() => {
          if (id !== requestId.current) return;
          setResults([]);
          setNote("Symbol search is unavailable — type the symbol directly.");
          setOpen(true);
        })
        .finally(() => {
          if (id === requestId.current) setLoading(false);
        });
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query, canSearch]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const pick = (item: SymbolSuggestion) => {
    suppress.current = true;
    onChange(item.query);
    onSubmit(item.query);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || results.length === 0) {
      if (e.key === "Enter") {
        // Nothing to pick — chart whatever was typed. This is the path that
        // keeps FUT:/FX: forms usable.
        e.preventDefault();
        onSubmit(value);
        setOpen(false);
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(results[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={boxRef} className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      {loading && (
        <Loader2 className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 animate-spin text-muted-foreground" />
      )}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => results.length > 0 && setOpen(true)}
        placeholder="AAPL · EUR.USD · BTC-USD · ES=F"
        spellCheck={false}
        autoComplete="off"
        aria-label="Symbol"
        aria-expanded={open}
        role="combobox"
        aria-controls="symbol-suggestions"
        className="h-9 w-72 rounded-md border border-input bg-transparent px-3 py-1 pl-8 pr-8 font-mono text-sm uppercase shadow-xs transition-colors outline-none placeholder:font-sans placeholder:text-muted-foreground placeholder:normal-case focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
      />

      {open && canSearch && (results.length > 0 || note) && (
        <div
          id="symbol-suggestions"
          role="listbox"
          className="absolute left-0 top-full z-50 mt-1 w-[26rem] max-w-[90vw] overflow-hidden rounded-md border border-border bg-popover shadow-lg"
        >
          <ul className="max-h-80 overflow-y-auto py-1">
            {results.map((item, i) => (
              <li key={`${item.query}-${i}`}>
                <button
                  type="button"
                  role="option"
                  aria-selected={i === active}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(item)}
                  className={cn(
                    "flex w-full items-start gap-2 px-3 py-1.5 text-left transition-colors",
                    i === active ? "bg-accent" : "hover:bg-accent/50"
                  )}
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="flex items-center gap-1.5">
                      <span className="font-mono text-sm font-medium">
                        {item.label}
                      </span>
                      {item.source === "watchlist" && (
                        <Star className="size-3 fill-primary text-primary" />
                      )}
                      {item.secType !== "STK" && (
                        <span className="rounded border border-border px-1 text-[9px] uppercase text-muted-foreground">
                          {item.secType === "CASH" ? "FX" : item.secType}
                        </span>
                      )}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {item.description}
                    </span>
                  </span>
                  {/* The exact string that will be charted — shown so a
                      foreign listing can't be mistaken for the US line. */}
                  {item.query !== item.label && (
                    <span className="shrink-0 font-mono text-[10px] text-muted-foreground/70">
                      {item.query}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>

          {note && (
            <div className="flex items-start gap-1.5 border-t border-border bg-unknown/5 px-3 py-1.5 text-[11px] text-unknown">
              <TriangleAlert className="mt-0.5 size-3 shrink-0" />
              {note}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
