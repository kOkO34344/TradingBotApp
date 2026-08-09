"use client";

/**
 * forecast-chart.tsx — history plus a predicted path, and the Monte Carlo fan.
 *
 * The forecast is drawn in a visually distinct colour and separated from the
 * last real bar by a marker, so there is never a moment where a prediction
 * could be mistaken for a print. That matters more here than usual: these
 * candles are model output with no measured forecasting skill behind them
 * (IC 0.036, hit rate 50.0%), and the chart should not lend them the
 * authority that a normal price chart carries.
 *
 * The fan chart draws every sampled path individually rather than a mean
 * with error bars. Averaging is what hides the problem — the point is to see
 * how far apart the draws actually are.
 *
 * It draws three layers: a P10-P90 envelope, the individual paths coloured by
 * whether they finish above or below today's close, and the median on top.
 * The envelope alone would be the conventional rendering and would be wrong
 * for this project — CLAUDE.md's open hypothesis is that single draws move
 * top-N membership around, so the raggedness IS the finding and a smooth band
 * would hide it.
 */

/** Paths drawn individually before the fan turns to mush. Above this the
 *  envelope still covers every path; only the individual lines are capped,
 *  and the UI says so. */
export const MAX_DRAWN_PATHS = 24;

/** History bars kept visible beside the fan. Enough to read the forecast
 *  against recent price action, few enough that the forecast is not a sliver
 *  at the right edge. */
const HISTORY_BARS_IN_FAN = 14;

import { useEffect, useRef } from "react";
import {
  AreaSeries,
  CandlestickSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type Time,
} from "lightweight-charts";

import type { Bar, IndicatorPoint } from "@/lib/api";

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

function alpha(rgb: string, a: number): string {
  const nums = rgb.match(/[\d.]+/g);
  if (!nums || nums.length < 3) return rgb;
  return `rgba(${nums[0]}, ${nums[1]}, ${nums[2]}, ${a})`;
}

function baseOptions() {
  const grid = cssVar("--chart-grid", "rgba(255,255,255,0.08)");
  return {
    layout: {
      background: { color: "transparent" },
      textColor: cssVar("--muted-foreground", "#888"),
      fontFamily: "var(--font-geist-sans), system-ui, sans-serif",
      attributionLogo: false,
    },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { borderColor: grid },
    timeScale: { borderColor: grid, timeVisible: false },
    crosshair: { mode: 1 },
    autoSize: true,
  };
}

export function ForecastChart({
  history,
  forecast,
  height = 300,
}: {
  history: Bar[];
  forecast: Bar[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container || history.length === 0) return;

    const up = cssVar("--chart-up", "#26a69a");
    const down = cssVar("--chart-down", "#ef5350");
    const pred = cssVar("--chart-1", "#e0a458");

    const chart = createChart(container, baseOptions());

    const hist = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    hist.setData(
      history.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    if (forecast.length) {
      // One flat signal colour for every predicted bar — not green/red by
      // direction. Colouring a forecast like a real candle invites reading
      // it as one.
      const fc = chart.addSeries(CandlestickSeries, {
        upColor: alpha(pred, 0.55),
        downColor: alpha(pred, 0.2),
        borderUpColor: pred,
        borderDownColor: pred,
        wickUpColor: pred,
        wickDownColor: pred,
        priceLineVisible: false,
      });
      fc.setData(
        forecast.map((b) => ({
          time: b.time as Time,
          open: b.open,
          high: b.high,
          low: b.low,
          close: b.close,
        }))
      );
      createSeriesMarkers(hist, [
        {
          time: history[history.length - 1].time as Time,
          position: "aboveBar",
          color: pred,
          shape: "arrowDown",
          text: "forecast →",
        },
      ]);
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [history, forecast]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}

export function FanChart({
  history,
  series,
  height = 420,
}: {
  history: Bar[];
  series: IndicatorPoint[][];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = ref.current;
    if (!container || history.length === 0) return;

    const up = cssVar("--chart-up", "#26a69a");
    const down = cssVar("--chart-down", "#ef5350");
    const pred = cssVar("--chart-1", "#e0a458");
    // OPAQUE, unlike every other chart in the app, and it has to be: the
    // P10-P90 ribbon is built by filling to P90 and masking back to P10, and
    // a mask needs something solid to paint. Over a translucent glass panel a
    // transparent pane would show the page through the mask instead.
    const bg = cssVar("--chart-surface", "#111318");

    const chart = createChart(container, {
      ...baseOptions(),
      layout: { ...baseOptions().layout, background: { color: bg } },
    });

    const hist = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderUpColor: up,
      borderDownColor: down,
      wickUpColor: up,
      wickDownColor: down,
    });
    hist.setData(
      history.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    );

    // ---------------------------------------------------------- the fan
    //
    // Three layers, drawn back to front: the P10-P90 envelope as a soft fill,
    // then every individual path coloured by where it FINISHES, then the
    // median on top. The bands give you the shape at a glance; the individual
    // paths are kept because this project's open hypothesis is precisely that
    // single draws disagree with each other, and a smooth band would hide the
    // raggedness that finding is about.
    const finished = series.filter((p) => p.length > 0);

    // Per-timestamp percentiles across paths. Paths share a time grid (they
    // are the same forecast horizon), so this indexes positionally rather
    // than joining on time — cheaper and exact for this input.
    const steps = Math.max(...finished.map((p) => p.length), 0);
    const pct = (sorted: number[], q: number) =>
      sorted.length === 0
        ? 0
        : sorted[Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))];

    const band: { time: Time; low: number; high: number; mid: number }[] = [];
    for (let i = 0; i < steps; i += 1) {
      const col = finished
        .map((p) => p[i]?.value)
        .filter((v): v is number => typeof v === "number")
        .sort((a, b) => a - b);
      if (col.length === 0) continue;
      const t = finished.find((p) => p[i])?.[i]?.time;
      if (t === undefined) continue;
      band.push({
        time: t as Time,
        low: pct(col, 0.1),
        high: pct(col, 0.9),
        mid: pct(col, 0.5),
      });
    }

    // The envelope, as two area series sharing a baseline. lightweight-charts
    // has no band primitive, so the P90 area is filled and the P10 area is
    // painted back in the chart's own background — the visible result is a
    // filled ribbon between them.
    if (band.length > 1) {
      const hi = chart.addSeries(AreaSeries, {
        lineColor: "transparent",
        topColor: alpha(pred, 0.24),
        bottomColor: alpha(pred, 0.24),
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      hi.setData(band.map((b) => ({ time: b.time, value: b.high })));

      const lo = chart.addSeries(AreaSeries, {
        lineColor: "transparent",
        topColor: bg,
        bottomColor: bg,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      lo.setData(band.map((b) => ({ time: b.time, value: b.low })));
    }

    // Individual paths, coloured by outcome. Green if the draw finishes above
    // where price is now, pink if below — so "how many went up" is legible
    // from the picture instead of only from the stat line above it.
    const start = history[history.length - 1]?.close;
    for (const path of finished.slice(0, MAX_DRAWN_PATHS)) {
      const last = path[path.length - 1]?.value;
      const rising = start === undefined || last === undefined || last >= start;
      const line = chart.addSeries(LineSeries, {
        color: alpha(rising ? up : down, 0.55),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(path.map((p) => ({ time: p.time as Time, value: p.value })));
    }

    // Median last, so it sits above everything.
    if (band.length > 1) {
      const med = chart.addSeries(LineSeries, {
        color: pred,
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      med.setData(band.map((b) => ({ time: b.time, value: b.mid })));
    }

    // ZOOM TO THE FORECAST, do not fitContent().
    //
    // `fitContent()` fits five months of daily history against a five-day
    // forecast, which puts the entire fan in the last ~8% of the width — the
    // paths become a scribble and the P10-P90 ribbon is too small to see at
    // all. That was the actual readability problem here; line width was a
    // symptom. Showing a short run-up plus the whole forecast gives the fan
    // most of the canvas while keeping enough history to read it against.
    const tail = Math.min(history.length, HISTORY_BARS_IN_FAN);
    chart.timeScale().setVisibleLogicalRange({
      from: history.length - tail,
      to: history.length + steps + 1,
    });

    return () => chart.remove();
  }, [history, series]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
