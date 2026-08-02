"use client";

/**
 * charts/page.tsx — the charting screen.
 *
 * Symbol box accepts all five asset classes the bot can trade (see
 * api/contracts.py for the accepted spellings). Bars, indicators, levels
 * and trade markers arrive in one request so the chart never renders price
 * before its overlays and flickers, and so a timeframe flip costs IBKR one
 * paced historical request rather than four.
 *
 * The data-source strip under the chart is deliberate. This account has no
 * live market-data subscription, so everything here is delayed; the strip
 * says so, names the source, and shows the age of the pull. A trading
 * screen that implies "live" when it isn't is the kind of quiet wrongness
 * this project keeps having to dig out of the journal afterwards.
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock,
  Loader2,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";

import {
  api,
  type BarsResponse,
  type IndicatorCatalogEntry,
  type Position,
} from "@/lib/api";
import { useFetch, useLive } from "@/lib/use-live";
import { DASH, fmtAge, fmtPct, fmtPrice, fmtTime, pnlClass } from "@/lib/format";
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

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"];
const DEFAULT_INDICATORS = ["sma:20", "ema:50"];

const STORAGE_KEY = "tradingbot.charts.v1";

interface ChartPrefs {
  symbol: string;
  timeframe: string;
  indicators: string[];
  showLevels: boolean;
}

const DEFAULT_PREFS: ChartPrefs = {
  symbol: "AAPL",
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

export default function ChartsPage() {
  const live = useLive();
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
  const watchlist = useFetch(() => api.watchlist(), []);
  // Deliberately not swallowed with a `.catch(() => [])`: if this fails, the
  // chart genuinely does not know whether the symbol is held or where its
  // stop is, and the header says so instead of silently showing nothing —
  // which is indistinguishable from "you hold no position here".
  const positions = useFetch(() => api.positions(), [live.revisions.positions]);

  const bars = useFetch<BarsResponse>(
    () =>
      api.bars({
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

  const held: Position | undefined = positions.data?.positions.find(
    (p) => p.symbol === chartData?.label
  );

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
          title="Refetch from IBKR (bypasses the bar cache TTL)"
        >
          <RefreshCw className={cn("size-4", bars.loading && "animate-spin")} />
        </Button>

        <div className="ml-auto flex items-center gap-1.5 overflow-x-auto">
          {(watchlist.data?.tickers ?? []).slice(0, 14).map((t) => (
            <button
              key={t}
              onClick={() => {
                setSymbol(t);
                setSymbolInput(t);
              }}
              className={cn(
                "rounded px-2 py-1 font-mono text-xs transition-colors",
                symbol === t
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              )}
            >
              {t}
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
              HOLDING {held.position}
            </Badge>
            <StopBadge position={held} />
          </div>
        )}

        {positions.error && (
          <Badge
            variant="outline"
            className="border-unknown/50 text-unknown gap-1"
            title={positions.error.message}
          >
            <AlertTriangle className="size-3" />
            POSITION STATE UNKNOWN
          </Badge>
        )}

        {chartData && (
          <div className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {chartData.count} bars · {chartData.duration}
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
              <div className="space-y-1">
                <p className="font-medium text-loss">Could not load {symbol}</p>
                <p className="text-sm text-muted-foreground">
                  {bars.error.message}
                </p>
              </div>
            </div>
          </Card>
        )}

        {!bars.error && bars.loading && !chartData && (
          <div className="flex h-full items-center justify-center gap-2 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading bars from IBKR…
          </div>
        )}

        {chartData && (
          <PriceChart data={chartData} positions={positions.data?.positions} />
        )}
      </div>

      {/* ------------------------------------------------------ source strip */}
      {chartData && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-xs text-muted-foreground">
          <span>
            Source <span className="text-foreground">{chartData.source}</span>
          </span>
          {chartData.delayed && (
            <Badge
              variant="outline"
              className="border-unknown/40 text-unknown text-[10px]"
              title="This account has no live market-data subscription; IBKR serves delayed data (reqMarketDataType 3)."
            >
              DELAYED
            </Badge>
          )}
          <span>
            Fetched {fmtAge(chartData.ageSeconds)}
            {chartData.fromCache && " (cached)"}
          </span>
          <span>
            Last bar{" "}
            <span className="text-foreground">{fmtTime(lastBar?.time)}</span>
          </span>
          {chartData.stale && (
            <span className="text-unknown">
              Stale — IBKR request failed, showing the previous pull:{" "}
              {chartData.error}
            </span>
          )}
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

function StopBadge({ position }: { position: Position }) {
  if (position.protected === null) {
    return (
      <Badge
        variant="outline"
        className="border-unknown/50 text-unknown gap-1"
        title={position.protectionReason}
      >
        <AlertTriangle className="size-3" />
        STOP UNKNOWN
      </Badge>
    );
  }
  if (!position.protected) {
    return (
      <Badge variant="destructive" className="gap-1" title={position.protectionReason}>
        <AlertTriangle className="size-3" />
        UNPROTECTED
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="border-profit/40 text-profit gap-1"
      title={position.protectionReason}
    >
      <Check className="size-3" />
      GTC STOP
    </Badge>
  );
}

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
