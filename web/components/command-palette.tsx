"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Cmd-K command palette.
 *
 * Deliberately hand-rolled rather than pulled from shadcn's `Command`. That
 * component wraps Base UI's dialog, and web/CLAUDE.md records two Base UI
 * traps that type-check cleanly and then fail at runtime (menu items firing
 * `onClick` not `onSelect`, `DropdownMenuLabel` throwing outside a
 * `DropdownMenuGroup`). A palette is keyboard-only surface area where a
 * silent no-op handler is the whole failure mode, so this uses plain
 * elements whose behaviour is visible in this file.
 *
 * Navigation only, and it stays that way. The single write this UI still has
 * is arming the unattended runner, which lives behind a confirmation dialog in
 * the header — a fuzzy-matched keystroke is exactly the wrong way to reach it.
 */

interface Cmd {
  id: string;
  label: string;
  hint: string;
  href: string;
  keywords: string;
}

// Four destinations, matching the nav. The old eight-entry list is kept alive
// only as keywords: typing "positions" or "rebalance" still has to land
// somewhere, because that is what a palette is for — you type the word you
// have in your head, not the one the nav settled on this week.
const COMMANDS: Cmd[] = [
  { id: "watch", label: "Watch", hint: "the account and the night", href: "/watch",
    keywords: "watch ftmo venue equity drawdown daily limit prop challenge "
      + "night band wakeups positions holdings stops protected dashboard "
      + "overview account summary" },
  { id: "signal", label: "Signal", hint: "Kronos and the plan", href: "/signal",
    keywords: "signal kronos forecast predict model monte carlo rebalance "
      + "rotate proposal target ranking spread plan" },
  { id: "market", label: "Market", hint: "price + indicators", href: "/market",
    keywords: "market charts price candles indicator sma ema bollinger atr bars" },
  { id: "ledger", label: "Ledger", hint: "journal + backtests", href: "/ledger",
    keywords: "ledger journal trades log fills blocked audit history record "
      + "backtest strategy results momentum performance evidence" },
];

/** Subsequence match: "ftm" hits "FTMO", "reb" hits "Rebalance". */
function score(cmd: Cmd, q: string): number {
  if (!q) return 1;
  const needle = q.toLowerCase();
  const hay = `${cmd.label} ${cmd.keywords}`.toLowerCase();
  if (hay.includes(needle)) return 100 - hay.indexOf(needle);
  let i = 0;
  for (const ch of hay) if (ch === needle[i]) i++;
  return i === needle.length ? 1 : 0;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [rawActive, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const results = useMemo(
    () =>
      COMMANDS.map((c) => ({ c, s: score(c, q) }))
        .filter((r) => r.s > 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.c),
    [q],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        setQ("");
        setActive(0);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // Clamp rather than reset: re-clamping on every keystroke would fight the
  // arrow keys as the result list shrinks under the selection.
  //
  // Derived during render, NOT written back with setState in an effect. The
  // effect version was a cascading render — React would paint one frame with
  // the stale out-of-range index before the correction landed — and it is the
  // pattern `set-state-in-effect` exists to catch. Nothing else needs the
  // clamped value to live in state, so it doesn't.
  const active = Math.min(rawActive, Math.max(0, results.length - 1));

  if (!open) return null;

  const go = (href: string) => {
    setOpen(false);
    router.push(href);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-background/70 pt-[12vh] backdrop-blur-[2px]"
      onClick={() => setOpen(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-lg border hairline border-border bg-popover shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && results[active]) {
              e.preventDefault();
              go(results[active].href);
            }
          }}
          placeholder="Go to…"
          className="w-full bg-transparent px-4 py-3 text-sm outline-none placeholder:text-muted-foreground"
        />
        <div className="border-t hairline border-border">
          {results.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              No match.
            </p>
          ) : (
            results.map((c, i) => (
              <button
                key={c.id}
                onClick={() => go(c.href)}
                onMouseEnter={() => setActive(i)}
                className={`flex w-full items-baseline justify-between px-4 py-2 text-left text-sm ${
                  i === active ? "bg-secondary text-foreground" : "text-muted-foreground"
                }`}
              >
                <span>{c.label}</span>
                <span className="text-xs text-muted-foreground/70">{c.hint}</span>
              </button>
            ))
          )}
        </div>
        <div className="flex justify-between border-t hairline border-border px-4 py-1.5 text-[10px] uppercase tracking-widest text-muted-foreground/60">
          <span>↑↓ move · ↵ open · esc close</span>
          <span>navigation only</span>
        </div>
      </div>
    </div>
  );
}
