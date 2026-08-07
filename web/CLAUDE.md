# web/ — project memory for the UI

The root `CLAUDE.md` governs everything here too. This file only adds what is
specific to the frontend. `README.md` in this folder has the architecture,
the screen list and the full rationale for each rule below — read it before
changing anything structural.

@AGENTS.md

## Framework facts that differ from training data

- **Next.js 16** ships its own docs at `node_modules/next/dist/docs/`. Read
  the relevant guide before writing framework-level code.
- **shadcn now generates Base UI components**, not Radix. Three traps, and
  only the first is caught by `tsc` — the other two shipped broken and were
  found by clicking:
  - Compose with a `render` prop (`<DropdownMenuTrigger render={<Button />}>`),
    not `asChild`. Using `asChild` IS a type error.
  - **Menu items fire `onClick`, not `onSelect`.** `onSelect` type-checks
    (it's a real DOM handler on a div) and silently does nothing — the menu
    closes and no handler runs.
  - **`DropdownMenuLabel` must be inside `DropdownMenuGroup`.** Base UI's
    `GroupLabel` reads `MenuGroupContext` and throws a runtime error without
    one; Radix allowed a bare label.
  Lesson: after touching any shadcn component, OPEN it in the browser and
  click it. Typechecking a Base UI migration proves very little.
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
6. **A number that can be negative must be able to render negative.**
   `fmtPct(v, d, signed=false)` means "no + on positives", NOT `Math.abs`.
   An earlier version applied abs and showed a −2.3% backtest CAGR as
   "2.3%" — losses as gains, on the screen meant to report losses honestly.
7. **Don't read `localStorage` during the first client render.** The server
   couldn't have, so React reports a hydration mismatch and discards the
   subtree. Restore preferences in an effect after mount.
8. **On a failed fetch, don't keep showing the previous subject's data
   under the new subject's heading.** `useFetch` retains last-good data
   deliberately; screens must null it out on error (see `chartData` in
   `app/charts/page.tsx`) or they attribute AAPL's price to whatever you
   just failed to load.
9. **The charts screen is an FTMO screen** (moved 2026-08-07). It reads
   `/api/ftmo/bars`, not `/api/bars` — the IBKR path returned
   `ConnectionRefusedError ... 4002` on every request once Gateway went down,
   and rule 9 retired that venue, so it was never coming back. `/api/bars`
   still exists and still works when Gateway is up; nothing was deleted.
10. **A screen that has moved venue must be removed from the IB Gateway
    banner's scope.** `app-shell.tsx` keeps an explicit `isFtmoBacked()` list
    rather than a `startsWith("/ftmo")` test, because the moment a
    non-`/ftmo` route started reading from FTMO that test silently became the
    wrong question — the banner claimed "no data will load" over a screen
    that was loading fine.
11. **Which venue a journal row belongs to is load-bearing, not decoration.**
    The journal holds both brokers and they share ticker spellings: an IBKR
    AAPL share is not an FTMO AAPL CFD. `markers_for()` takes a `venue`
    filter and the charts screen passes it — without that, one venue's fills
    are drawn on the other's chart, asserting a trade that never happened
    there. Rows written before the venue column existed store `""` and are
    IBKR by construction; they are displayed as `ibkr` but dimmed, because
    that value was inferred from age rather than recorded.
12. **Both venues' event vocabularies have to be listed, or one disappears.**
    IBKR writes `RESULT`/`CLOSE_FILLED` with status `filled`; the FTMO runner
    writes `RESULT` with status `accepted` and `EXIT` for a rotation close.
    `journal_api`'s `FILL_EVENTS`/`FILLED_STATUSES` knew only IBKR's until
    2026-08-07, so every FTMO fill scored zero chart markers — correctly
    journalled, and undrawable.
