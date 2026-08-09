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
 */

import { useEffect, useRef } from "react";
import {
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
  height = 340,
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

    // Every path at low opacity: where they overlap the fan reads dark, where
    // they diverge it reads thin. That density IS the uncertainty.
    for (const path of series) {
      if (!path.length) continue;
      const line = chart.addSeries(LineSeries, {
        color: alpha(pred, 0.35),
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(
        path.map((p) => ({ time: p.time as Time, value: p.value }))
      );
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [history, series]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}
