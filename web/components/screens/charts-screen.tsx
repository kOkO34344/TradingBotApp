"use client";

/**
 * charts/page.tsx — the charting screen, on the FTMO venue.
 *
 * MOVED OFF IBKR 2026-08-07. It used to chart IBKR contracts through
 * `/api/bars`, which stopped working the moment IB Gateway went down — and
 * IBKR is retired in place (rule 9), so that was never coming back. Every
 * request returned `ConnectionRefusedError [Errno 61] ... 4002` and the screen
 * was a permanent error card. Bars now come from the venue this project
 * actually trades, through `/api/ftmo/bars`.
 *
 * Charting the traded instrument rather than a proxy is the point, not a
 * side effect: FTMO's instruments are CFDs (`US30.cash`, `NATGAS.cash`) with
 * no yfinance or IBKR equivalent, and Kronos forecasts the same series this
 * draws.
 *
 * Bars, indicators, levels and trade markers still arrive in one request so
 * the chart never paints price before its overlays and flickers.
 *
 * Markers are filtered to `venue="ftmo"`. The journal holds both brokers now,
 * and an IBKR AAPL share is not an FTMO AAPL CFD — drawing one venue's fills
 * on the other's chart would assert something that never happened there.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  Clock,
  Loader2,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";

import {
  api,
  ftmo as ftmoApi,
  type FtmoBarsResponse,
  type IndicatorCatalogEntry,
} from "@/lib/api";
import { useFtmoStream } from "@/lib/use-ftmo";
import { useFetch } from "@/lib/use-live";
import { DASH, fmtPct, fmtPrice, fmtTime, pnlClass } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { PriceChart } from "@/components/chart/price-chart";
import { SymbolSearch } from "@/components/symbol-search";

const TIMEFRAMES = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];
const DEFAULT_INDICATORS = ["sma:20", "ema:50"];

// v2 because v1 stored an IBKR ticker. A saved "SPY" or "NVDA" is not an FTMO
// instrument, so restoring it after the venue move greeted you with
// `'SPY' is not in the symbol capture` and a chart that looked broken — with
// no clue that the fix was simply to pick a different symbol. Bumping the key
// retires those saved values instead of trying to translate them; there is no
// honest mapping from a US share to a CFD universe.
const STORAGE_KEY = "tradingbot.charts.v2";

interface ChartPrefs {
  symbol: string;
  timeframe: string;
  indicators: string[];
  showLevels: boolean;
}

const DEFAULT_PREFS: ChartPrefs = {
  // An FTMO instrument name, not an IBKR ticker. EURUSD is the venue's most
  // liquid symbol and the one whose 5-digit pricing exercises the precision
  // path that a 2-digit default would hide.
  symbol: "EURUSD",
  timeframe: "1d",
  indicators: DEFAULT_INDICATORS,
  showLevels: true,
};

/**
 * Read the saved chart setup — a charting screen that forgets what you were
 * looking at every reload is not one you'll keep open.
 *
 * Applied in an effect, NOT as lazy initial state. Lazy init reads
 * localStorage during the first client render, which the server could not
 * have done, so React reports a hydration mismatch and throws the whole
 * subtree away. Restoring after mount means server and first client render
 * agree on the defaults.
 */
function readPrefs(): ChartPrefs {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return DEFAULT_PREFS;
    const parsed = JSON.parse(saved);
    return {
      symbol: typeof parsed.symbol === "string" ? parsed.symbol : DEFAULT_PREFS.symbol,
      timeframe:
        typeof parsed.timeframe === "string" ? parsed.timeframe : DEFAULT_PREFS.timeframe,
      indicators: Array.isArray(parsed.indicators)
        ? parsed.indicators
        : DEFAULT_PREFS.indicators,
      showLevels:
        typeof parsed.showLevels === "boolean"
          ? parsed.showLevels
          : DEFAULT_PREFS.showLevels,
    };
  } catch {
    return DEFAULT_PREFS;   // a corrupt preference is not worth surfacing
  }
}

export function ChartsScreen() {
  const ftmo = useFtmoStream();
  const [symbolInput, setSymbolInput] = useState(DEFAULT_PREFS.symbol);
  const [symbol, setSymbol] = useState(DEFAULT_PREFS.symbol);
  const [timeframe, setTimeframe] = useState(DEFAULT_PREFS.timeframe);
  const [indicators, setIndicators] = useState<string[]>(DEFAULT_PREFS.indicators);
  const [showLevels, setShowLevels] = useState(DEFAULT_PREFS.showLevels);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [restored, setRestored] = useState(false);

  useEffect(() => {
    // Runs once, after mount, so hydration sees the same defaults the server
    // rendered. The `restored` flag then unblocks saving, so this initial
    // pass can't write the defaults back over the saved preferences.
    const prefs = readPrefs();
    /* eslint-disable react-hooks/set-state-in-effect */
    setSymbol(prefs.symbol);
    setSymbolInput(prefs.symbol);
    setTimeframe(prefs.timeframe);
    setIndicators(prefs.indicators);
    setShowLevels(prefs.showLevels);
    setRestored(true);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, []);

  useEffect(() => {
    if (!restored) return;   // don't overwrite saved prefs with the defaults
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ symbol, timeframe, indicators, showLevels })
    );
  }, [restored, symbol, timeframe, indicators, showLevels]);

  const catalog = useFetch<{ indicators: IndicatorCatalogEntry[] }>(
    () => api.indicatorCatalog(),
    []
  );
  // The venue's own tradeable universe replaces the IBKR watchlist. Those
  // were US stock tickers; none of them exist on this broker.
  const universe = useFetch(() => ftmoApi.universe(), []);
  // Every chartable instrument, for the search box. Wider than the universe:
  // all 202 can be charted, only ~14 are configured for trading.
  const allSymbols = useFetch(() => ftmoApi.symbols(), []);

  const suggest = useCallback(
    (q: string) => {
      const needle = q.trim().toUpperCase();
      const rows = allSymbols.data?.symbols ?? [];
      return rows
        .filter((s) => s.symbol.toUpperCase().includes(needle))
        .slice(0, 12)
        .map((s) => ({
          query: s.symbol,
          symbol: s.symbol,
          label: s.symbol,
          description: s.assetClass
            ? `${s.assetClass} · traded universe`
            : "CFD",
          secType: "cfd",
          exchange: "FTMO",
          currency: s.quoteAsset || "",
          source: "ftmo" as const,
        }));
    },
    [allSymbols.data]
  );

  // Positions ride the same WebSocket the /ftmo screen uses, so the chart and
  // the venue screen cannot disagree about what is held.
  const positions = ftmo.snap?.positions ?? [];

  const bars = useFetch<FtmoBarsResponse>(
    () =>
      ftmoApi.bars({
        symbol,
        timeframe,
        indicators,
        levels: showLevels,
        markers: true,
      }),
    [symbol, timeframe, indicators.join(","), showLevels, reloadNonce]
  );

  const submit = (value: string) => {
    const next = value.trim().toUpperCase();
    if (next) setSymbol(next);
  };

  // `useFetch` keeps the last good data through an error on purpose, but the
  // header must not then attribute AAPL's name and price to the symbol you
  // just asked for and failed to load. On error the chart data is treated as
  // absent rather than shown under the new symbol's heading.
  const chartData = bars.error ? null : bars.data;

  // The venue's own words for "I don't carry that", from
  // `ftmo_session.trendbars`. Matched on the message rather than a status code
  // because the backend reports every venue refusal as a 502 — this one is the
  // user's typo, not a broken connection, and the two deserve different help.
  const notOnVenue = Boolean(
    bars.error?.message?.includes("not in the symbol capture")
  );
  const held = positions.find((p) => p.symbol === symbol);

  // Resolved here rather than inside the chart, because "is this stop
  // durable?" is a venue question. An FTMO stop is a field on the position
  // itself and cannot expire, unlike an IBKR DAY stop — so a stop that exists
  // here IS durable, and one that is missing is drawn as nothing rather than
  // as false comfort.
  const stopLines = held?.stopLoss
    ? [{ price: held.stopLoss, title: "STOP", durable: true }]
    : [];

  const lastBar = chartData?.bars.at(-1);
  const prevBar = chartData?.bars.at(-2);
  const change =
    lastBar && prevBar ? ((lastBar.close - prevBar.close) / prevBar.close) * 100 : null;

  const failedIndicators = useMemo(
    () => (bars.data?.indicators ?? []).filter((i) => i.error),
    [bars.data]
  );

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {/* ---------------------------------------------------------- toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <SymbolSearch
          value={symbolInput}
          onChange={setSymbolInput}
          onSubmit={submit}
          suggest={suggest}
        />

        <div className="flex items-center rounded-md border border-border p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={cn(
                "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                timeframe === tf
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              {tf}
            </button>
          ))}
        </div>

        <IndicatorPicker
          catalog={catalog.data?.indicators ?? []}
          active={indicators}
          onChange={setIndicators}
        />

        <Button
          variant={showLevels ? "secondary" : "ghost"}
          size="sm"
          onClick={() => setShowLevels((v) => !v)}
          title="Swing support/resistance from indicators.py"
        >
          Levels
        </Button>

        <Button
          variant="ghost"
          size="icon"
          onClick={() => setReloadNonce((n) => n + 1)}
          title="Refetch trendbars from FTMO"
        >
          <RefreshCw className={cn("size-4", bars.loading && "animate-spin")} />
        </Button>

        <div className="ml-auto flex items-center gap-1.5 overflow-x-auto">
          {(universe.data?.universe ?? []).map((u) => (
            <button
              key={u.symbol}
              onClick={() => {
                setSymbol(u.symbol);
                setSymbolInput(u.symbol);
              }}
              title={`${u.assetClass} · ${u.digits}dp`}
              className={cn(
                "rounded px-2 py-1 font-mono text-xs whitespace-nowrap transition-colors",
                symbol === u.symbol
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              {u.symbol}
            </button>
          ))}
        </div>
      </div>

      {/* ------------------------------------------------------- header row */}
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-b border-border px-4 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-xl font-semibold tracking-tight">
            {chartData?.label ?? symbol}
          </h1>
          {chartData && (
            <Badge variant="outline" className="text-[10px] uppercase">
              {chartData.kind}
            </Badge>
          )}
        </div>

        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-semibold tabular-nums">
            {lastBar ? fmtPrice(lastBar.close, chartData?.kind) : DASH}
          </span>
          <span className={cn("text-sm tabular-nums", pnlClass(change))}>
            {fmtPct(change)}
          </span>
        </div>

        {held && (
          <div className="flex items-center gap-2 text-sm">
            <Badge variant="secondary" className="font-mono">
              {held.side} {held.units.toLocaleString()}
            </Badge>
            {held.stopLoss ? (
              <Badge variant="outline" className="border-profit/50 text-profit font-mono">
                STOP {fmtPrice(held.stopLoss, chartData?.kind)}
              </Badge>
            ) : (
              <Badge variant="outline" className="border-loss/50 text-loss gap-1">
                <AlertTriangle className="size-3" />
                UNPROTECTED
              </Badge>
            )}
          </div>
        )}

        {/* Rule 1: a dropped socket is missing information, not "flat". The
            last frame is still on screen, so say it may be stale rather than
            letting an absent position read as no position. */}
        {!ftmo.live && (
          <Badge variant="outline" className="border-unknown/50 text-unknown gap-1">
            <AlertTriangle className="size-3" />
            POSITION STATE UNKNOWN
          </Badge>
        )}

        {chartData && (
          <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {chartData.count} bars · {chartData.period}
            </span>
          </div>
        )}
      </div>

      {/* ------------------------------------------------------------ chart */}
      <div className="relative flex-1 min-h-0 px-2 py-2">
        {bars.error && (
          <Card className="m-4 border-loss/40 bg-loss/5 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 size-5 shrink-0 text-loss" />
              <div className="space-y-2">
                <p className="font-medium text-loss">Could not load {symbol}</p>
                <p className="text-sm text-muted-foreground">
                  {bars.error.message}
                </p>
                {/* A symbol this venue does not carry is a recoverable mistake,
                    so the recovery is offered here rather than left as an
                    error to stare at. IBKR tickers are the likely cause: this
                    screen used to chart them. */}
                {notOnVenue && (
                  <div className="space-y-1.5 pt-1">
                    <p className="text-sm text-muted-foreground">
                      <span className="font-medium text-foreground">
                        {symbol}
                      </span>{" "}
                      is not an FTMO instrument. This screen charted IBKR
                      tickers until 2026-08-07 — the venue trades CFDs like{" "}
                      <span className="font-mono">EURUSD</span> and{" "}
                      <span className="font-mono">US30.cash</span>. Pick one:
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {(universe.data?.universe ?? []).slice(0, 14).map((u) => (
                        <Button
                          key={u.symbol}
                          size="sm"
                          variant="outline"
                          className="h-7 font-mono text-xs"
                          onClick={() => {
                            setSymbol(u.symbol);
                            setSymbolInput(u.symbol);
                          }}
                        >
                          {u.symbol}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </Card>
        )}

        {!bars.error && bars.loading && !chartData && (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading trendbars from FTMO…
          </div>
        )}

        {chartData && <PriceChart data={chartData} stopLines={stopLines} />}
      </div>

      {/* ------------------------------------------------------ source strip */}
      {chartData && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-xs text-muted-foreground">
          <span>
            Source{" "}
            <span className="text-foreground">FTMO / cTrader {chartData.period}</span>
          </span>
          {chartData.delayed && (
            <Badge
              variant="outline"
              className="border-unknown/40 text-unknown text-[10px]"
            >
              DELAYED
            </Badge>
          )}
          <span>
            Last bar{" "}
            <span className="text-foreground">{fmtTime(lastBar?.time)}</span>
          </span>
          {/* The FTMO session streams; it has no bar cache to age or go stale,
              so there is no "fetched N ago (cached)" to report here. Showing a
              fabricated age would be worse than showing none. */}
          {failedIndicators.map((ind) => (
            <span key={ind.id} className="text-unknown">
              {ind.name}: {ind.error}
            </span>
          ))}
          {chartData.markers.length > 0 && (
            <span>
              {chartData.markers.length} journal marker
              {chartData.markers.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

/*
 * The IBKR-shaped `StopBadge` that used to live here is gone. Its three states
 * existed because IBKR reports a stop as a SEPARATE order that can be missing,
 * DAY-scoped, or unknowable when `reqAllOpenOrders` wedges. An FTMO stop is a
 * field on the position itself, returned in the same frame, so "unknown" is
 * not a state this venue can be in — and rendering an amber UNKNOWN that can
 * never occur would be inventing doubt rather than reporting it.
 */

function IndicatorPicker({
  catalog,
  active,
  onChange,
}: {
  catalog: IndicatorCatalogEntry[];
  active: string[];
  onChange: (next: string[]) => void;
}) {
  const add = (entry: IndicatorCatalogEntry) => {
    // Default params come from the server catalog, so the UI never invents
    // a period that indicators.py wouldn't have used.
    const params = Object.values(entry.params);
    const spec = params.length
      ? `${entry.key}:${params.map((p) => (Number.isInteger(p) ? p : p)).join(":")}`
      : entry.key;
    if (!active.includes(spec)) onChange([...active, spec]);
  };

  return (
    <div className="flex items-center gap-1.5">
      <DropdownMenu>
        {/* shadcn's dropdown is Base UI, which composes via `render`
            rather than Radix's `asChild`. */}
        <DropdownMenuTrigger
          render={<Button variant="outline" size="sm" className="gap-1" />}
        >
          <Plus className="size-3.5" />
          Indicator
          <ChevronDown className="size-3.5 opacity-60" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-72">
          {/* The label must sit inside a Group: Base UI's GroupLabel reads
              MenuGroupContext and throws without one. Radix allowed a bare
              label, which is why this looked fine until it was opened. */}
          <DropdownMenuGroup>
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
              Computed server-side by indicators.py — identical to what the
              research agent sees.
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {catalog.map((entry) => (
              <DropdownMenuItem
                key={entry.key}
                // Base UI's MenuItem fires `onClick`, not Radix's `onSelect`.
                // TypeScript accepts `onSelect` (it's a real DOM handler on a
                // div) so this failed silently: the menu closed and nothing
                // was added. Same trap as asChild vs render.
                onClick={() => add(entry)}
                className="flex-col items-start gap-0.5"
              >
                <span className="font-medium">{entry.name}</span>
                <span className="text-xs text-muted-foreground">
                  {entry.description}
                </span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      {active.map((spec) => (
        <button
          key={spec}
          onClick={() => onChange(active.filter((s) => s !== spec))}
          className="group flex items-center gap-1 rounded-md border border-border px-2 py-1 font-mono text-xs text-muted-foreground transition-colors hover:border-loss/40 hover:text-loss"
          title="Remove"
        >
          {spec}
          <X className="size-3 opacity-0 transition-opacity group-hover:opacity-100" />
        </button>
      ))}
    </div>
  );
}
