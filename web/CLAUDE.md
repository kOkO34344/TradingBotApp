# web/ — project memory for the UI

The root `CLAUDE.md` governs everything here too. This file only adds what is
specific to the frontend. `README.md` in this folder has the architecture,
the screen list and the full rationale for each rule below — read it before
changing anything structural.

@AGENTS.md

## Framework facts that differ from training data

- **Next.js 16** ships its own docs at `node_modules/next/dist/docs/`. Read
  the relevant guide before writing framework-level code.
- **shadcn now generates Base UI components**, not Radix. They compose with
  a `render` prop (`<DropdownMenuTrigger render={<Button />}>`), not
  `asChild`. Using `asChild` is a type error, not a silent no-op.
- **lightweight-charts v5** uses `chart.addSeries(CandlestickSeries, opts,
  paneIndex)` and `createSeriesMarkers(series, markers)` — the v4
  `addCandlestickSeries()` / `series.setMarkers()` methods are gone.
- **lightweight-charts cannot parse `oklch()` / `lab()`**, which is what this
  theme is authored in and what `getComputedStyle` returns. Colours go
  through `toRgb()` in `components/chart/price-chart.tsx`, which rasterises
  one canvas pixel to force a conversion. Passing a theme variable straight
  to the chart throws "Failed to parse color" and kills the whole chart.

## Rules specific to this UI

1. **Never render a pending or failed request as a fact.** `—` for unknown,
   never `0`. A green "No exposure" tile over three live positions is the
   same failure class as the phantom liquidation this project already had.
2. **Stop protection has three states**: covered (green), unprotected (red),
   and UNKNOWN (amber, `protected === null`). Never collapse unknown into
   unprotected — a wedged `reqAllOpenOrders` is missing information, not a
   negative answer.
3. **No indicator math in JavaScript.** Everything comes from
   `indicators.py` via `/api/indicators`. This is a root-CLAUDE.md rule that
   explicitly names the web dashboard.
4. **Don't bump a WebSocket revision counter from an event that the fetch it
   triggers can itself cause.** `position` events are echoed by every
   `reqPositions` call; bumping on them created a UI↔broker feedback loop
   firing hundreds of requests a minute. See the comment in `lib/use-live.ts`.
5. **Local-only.** Never bind to `0.0.0.0`, never deploy. The backend can
   place orders and has no auth.
