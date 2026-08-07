"use client";

/**
 * price-chart.tsx — candlesticks, indicator overlays, sub-panes, trade markers.
 *
 * Every number drawn here was computed server-side by `indicators.py`. That
 * is a project rule, not a preference: the research agent's notes and the
 * terminal app quote the same functions, and an RSI that disagreed between
 * the chart and a research note would make the note's reasoning impossible
 * to audit. This component only draws what it is handed.
 *
 * Rendering choices worth knowing:
 *  - Indicators declaring a non-"price" pane get their own pane below the
 *    candles (lightweight-charts v5 panes), so RSI's 0-100 scale never
 *    squashes the price axis.
 *  - Trade markers come from trade_journal.csv, already filtered by the
 *    backend to exclude superseded and disputed rows — the chart must not
 *    assert a fill that the journal itself later retracted.
 *  - The live GTC stop is drawn as a price line, labelled with its TIF. A
 *    DAY stop would be drawn in the "unknown/warning" colour, because a
 *    stop that expires at the close is not protection.
 */

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
  type Time,
} from "lightweight-charts";

import type { Bar, IndicatorResult, Levels, TradeMarker } from "@/lib/api";

/**
 * Reads a CSS custom property and returns it as plain `rgb()`.
 *
 * The theme is authored in `oklch()`, which the browser reports back from
 * getComputedStyle as `oklab()`/`lab()` — and lightweight-charts' colour
 * parser understands neither, throwing "Failed to parse color" and killing
 * the whole chart. Rasterising one pixel through a canvas makes the browser
 * do the conversion for us and works for any colour syntax it supports.
 */
let probeCtx: CanvasRenderingContext2D | null = null;

function toRgb(color: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  if (!probeCtx) {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    probeCtx = canvas.getContext("2d", { willReadFrequently: true });
  }
  if (!probeCtx) return fallback;
  try {
    probeCtx.clearRect(0, 0, 1, 1);
    probeCtx.fillStyle = color;
    probeCtx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = probeCtx.getImageData(0, 0, 1, 1).data;
    return a === 255
      ? `rgb(${r}, ${g}, ${b})`
      : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
  } catch {
    return fallback;
  }
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  return raw ? toRgb(raw, fallback) : fallback;
}

/** Same colour at reduced opacity — string concatenation won't work on rgb(). */
function alpha(rgb: string, a: number): string {
  const nums = rgb.match(/[\d.]+/g);
  if (!nums || nums.length < 3) return rgb;
  return `rgba(${nums[0]}, ${nums[1]}, ${nums[2]}, ${a})`;
}

const SERIES_COLORS = ["--chart-1", "--chart-2", "--chart-3", "--chart-4", "--chart-5"];

/**
 * What this chart needs in order to draw, independent of which broker it came
 * from. `BarsResponse` (IBKR) and `FtmoBarsResponse` both satisfy it.
 *
 * Structural rather than a union of the two response types on purpose: the
 * chart should not be able to reach for `fromCache` or `duration`, which only
 * one venue has, and adding a third venue should not mean editing this file.
 */
export interface ChartPayload {
  label: string;
  kind: string;
  timeframe: string;
  bars: Bar[];
  indicators: IndicatorResult[];
  markers: TradeMarker[];
  levels: Levels | null;
  /** Price decimals, when the venue states them. Preferred over guessing. */
  digits?: number | null;
}

/**
 * A horizontal line to draw, already resolved by the caller.
 *
 * The caller decides, because "is this stop durable?" is a venue-specific
 * question: on IBKR it means `tif === "GTC"` (a DAY stop looks like protection
 * and evaporates at the close), while on FTMO the stop is a field on the
 * position itself and cannot expire. Encoding either rule in here would make
 * the chart wrong about the other venue.
 */
export interface ChartStopLine {
  price: number;
  title: string;
  /** false draws in the warning colour — looks like protection, isn't durable. */
  durable: boolean;
}

export function PriceChart({
  data,
  stopLines,
  showVolume = true,
  height = 520,
  themeKey,
}: {
  data: ChartPayload | null;
  stopLines?: ChartStopLine[];
  showVolume?: boolean;
  height?: number;
  /** Changing this forces a rebuild so theme switches repaint the chart. */
  themeKey?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

  // Panes are assigned by indicator, in the order they first appear, so two
  // RSIs with different periods share one pane instead of stacking two.
  const paneAssignment = useMemo(() => {
    const map = new Map<string, number>();
    let next = 1;
    for (const ind of data?.indicators ?? []) {
      if (ind.pane === "price" || ind.error) continue;
      if (!map.has(ind.pane)) map.set(ind.pane, next++);
    }
    return map;
  }, [data?.indicators]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !data || data.bars.length === 0) return;

    const text = cssVar("--muted-foreground", "#888");
    const grid = cssVar("--chart-grid", "rgba(255,255,255,0.08)");
    const up = cssVar("--chart-up", "#26a69a");
    const down = cssVar("--chart-down", "#ef5350");

    const chart = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: text,
        fontFamily: "var(--font-geist-sans), system-ui, sans-serif",
        attributionLogo: false,
        panes: { separatorColor: grid, separatorHoverColor: grid },
      },
      grid: {
        vertLines: { color: grid },
        horzLines: { color: grid },
      },
      rightPriceScale: { borderColor: grid },
      timeScale: {
        borderColor: grid,
        // Intraday timeframes need the time of day, daily bars don't.
        timeVisible: data.timeframe !== "1d",
        secondsVisible: false,
      },
      crosshair: { mode: 1 },
      autoSize: true,
    });
    chartRef.current = chart;

    const digits =
      data.digits ?? (data.kind === "forex" || data.kind === "fx" ? 5 : 2);

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
      priceFormat: {
        type: "price",
        // Prefer the venue's own answer. FTMO alone spans 2-digit indices,
        // 3-digit gas and 5-digit FX, so a kind-based guess would flatten
        // every EURUSD candle to a flat line at 2dp. The kind check stays as
        // the fallback for IBKR, which reports no digits.
        precision: digits,
        minMove: 1 / 10 ** digits,
      },
    });
    candles.setData(
      data.bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );
    candleRef.current = candles;

    // Contracts with no trades tape (forex) report no volume at all — draw
    // no volume pane rather than a flat band of zeros.
    const hasVolume = data.bars.some((b) => (b.volume ?? 0) > 0);
    if (showVolume && hasVolume) {
      const volume = chart.addSeries(
        HistogramSeries,
        {
          priceFormat: { type: "volume" },
          priceScaleId: "volume",
          color: cssVar("--chart-5", "#888"),
        },
        0
      );
      // Pin volume to the bottom fifth so it never competes with price.
      volume.priceScale().applyOptions({
        scaleMargins: { top: 0.82, bottom: 0 },
      });
      volume.setData(
        data.bars.map((b) => ({
          time: b.time as Time,
          value: b.volume ?? 0,
          color: b.close >= b.open ? alpha(up, 0.33) : alpha(down, 0.33),
        }))
      );
    }

    // ---- indicators
    let colorIndex = 0;
    for (const ind of data.indicators) {
      if (ind.error) continue;
      const paneIndex =
        ind.pane === "price" ? 0 : (paneAssignment.get(ind.pane) ?? 1);

      const outputs = Object.entries(ind.series);
      for (const [name, points] of outputs) {
        if (!points.length) continue;
        const color = cssVar(
          SERIES_COLORS[colorIndex % SERIES_COLORS.length],
          "#e0a458"
        );
        colorIndex += 1;

        if (name === "histogram") {
          const hist = chart.addSeries(
            HistogramSeries,
            { color, priceLineVisible: false, lastValueVisible: false },
            paneIndex
          );
          hist.setData(
            points.map((p) => ({
              time: p.time as Time,
              value: p.value,
              color: p.value >= 0 ? alpha(up, 0.6) : alpha(down, 0.6),
            }))
          );
          continue;
        }

        const line = chart.addSeries(
          LineSeries,
          {
            color,
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: ind.pane !== "price",
            title:
              outputs.length > 1
                ? `${ind.name} ${name}`
                : `${ind.name}${
                    ind.params.period ? ` ${ind.params.period}` : ""
                  }`,
          },
          paneIndex
        );
        line.setData(
          points.map((p) => ({ time: p.time as Time, value: p.value }))
        );

        // Fixed-scale panes (RSI 0-100) plus their reference lines.
        if (ind.bounds && ind.pane !== "price") {
          for (const guide of ind.guides ?? []) {
            line.createPriceLine({
              price: guide,
              color: grid,
              lineWidth: 1,
              lineStyle: 2,
              axisLabelVisible: false,
              title: "",
            });
          }
        }
      }
    }

    // Give sub-panes a sensible share of the height rather than an even split:
    // price stays dominant, each indicator pane gets a readable strip.
    const panes = chart.panes();
    if (panes.length > 1) {
      const available = container.clientHeight || height;
      const subHeight = Math.max(
        64,
        Math.round((available * 0.24) / Math.max(1, panes.length - 1))
      );
      for (let i = 1; i < panes.length; i++) panes[i].setHeight(subHeight);
    }

    // ---- trade markers from the journal
    if (data.markers.length) {
      createSeriesMarkers(
        candles,
        data.markers.map((m) => ({
          time: m.time as Time,
          position: m.kind === "entry" ? "belowBar" : "aboveBar",
          color: m.kind === "entry" ? up : down,
          shape: m.kind === "entry" ? "arrowUp" : "arrowDown",
          text: m.text,
        }))
      );
    }

    // ---- levels and the live stop
    priceLinesRef.current = [];
    if (data.levels) {
      for (const level of data.levels.supports ?? []) {
        priceLinesRef.current.push(
          candles.createPriceLine({
            price: level,
            color: alpha(up, 0.4),
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
            title: "S",
          })
        );
      }
      for (const level of data.levels.resistances ?? []) {
        priceLinesRef.current.push(
          candles.createPriceLine({
            price: level,
            color: alpha(down, 0.4),
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: false,
            title: "R",
          })
        );
      }
    }

    for (const stop of stopLines ?? []) {
      if (!stop.price) continue; // no trigger price known — draw nothing
      // A non-durable stop is drawn in the warning colour, not the normal
      // stop colour: it looks like protection and stops being protection.
      // That distinction is the whole point of the line.
      priceLinesRef.current.push(
        candles.createPriceLine({
          price: stop.price,
          color: stop.durable
            ? cssVar("--loss", "#ef5350")
            : cssVar("--unknown", "#e0a458"),
          lineWidth: 2,
          lineStyle: stop.durable ? 0 : 2,
          axisLabelVisible: true,
          title: stop.title,
        })
      );
    }

    chart.timeScale().fitContent();

    const onResize = () => chart.timeScale().fitContent();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
    };
  }, [data, stopLines, showVolume, height, paneAssignment, themeKey]);

  // `autoSize` makes the chart track this element, so the element is sized by
  // the layout (h-full inside a flex-1 parent) rather than a hardcoded pixel
  // height. `height` is only the fallback for measuring pane splits.
  return (
    <div ref={containerRef} className="h-full w-full" data-testid="price-chart" />
  );
}
