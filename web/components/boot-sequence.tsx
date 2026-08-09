"use client";

/**
 * boot-sequence.tsx — a CRT power-on, once per browser session.
 *
 * A horizontal line snaps open into a full-screen wash and fades, the way a
 * tube warms up. It runs for ~900ms and then unmounts completely, so it costs
 * nothing after the first paint.
 *
 * THREE THINGS IT DELIBERATELY DOES NOT DO:
 *
 *  - It never blocks. `pointer-events: none` throughout, and the app is fully
 *    interactive underneath from the first frame. An animation that gates
 *    access to a risk panel would be indefensible; this is a wash over a
 *    working screen, not a loading gate.
 *  - It runs ONCE PER SESSION, not per navigation. Seeing a boot sequence
 *    every time you switch tabs would be a joke that stops being funny in
 *    about four minutes.
 *  - It respects `prefers-reduced-motion` by not rendering at all.
 *
 * `sessionStorage` is read in an effect, never during the first client
 * render — the server could not have read it, so touching it during render is
 * a hydration mismatch and React discards the subtree (web/CLAUDE.md rule 7).
 * That is also why this starts as `null` and only ever turns itself on.
 */

import { useEffect, useState } from "react";

const KEY = "booted-v1";

export function BootSequence() {
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    try {
      if (sessionStorage.getItem(KEY)) return;
      sessionStorage.setItem(KEY, "1");
    } catch {
      // Private mode or storage disabled. Play it; a one-off animation is a
      // fine failure mode, an exception on mount is not.
    }
    // Deliberate, and the lint rule's usual advice does not apply. The
    // decision depends on sessionStorage, which the server could not have
    // read — touching it during render is a hydration mismatch and React
    // discards the subtree (web/CLAUDE.md rule 7). So it has to happen after
    // mount. It runs exactly once and settles immediately; there is no
    // cascade to avoid.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPlaying(true);
    const timer = setTimeout(() => setPlaying(false), 950);
    return () => clearTimeout(timer);
  }, []);

  if (!playing) return null;

  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 z-[9998] overflow-hidden"
    >
      {/* The tube warming: a hairline that expands vertically into a wash. */}
      <div
        className="boot-sweep absolute inset-0 origin-center"
        style={{
          background:
            "linear-gradient(180deg, transparent, color-mix(in oklch, var(--primary) 22%, transparent) 45%, color-mix(in oklch, var(--profit) 18%, transparent) 55%, transparent)",
        }}
      />
      {/* One bright scan bar riding down the screen. */}
      <div
        className="absolute inset-x-0 h-px"
        style={{
          background: "var(--primary)",
          boxShadow: "0 0 24px 2px var(--primary)",
          animation: "boot-bar 900ms cubic-bezier(0.2, 0.8, 0.2, 1) both",
        }}
      />
    </div>
  );
}
