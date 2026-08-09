/**
 * api.ts — typed client for the local FastAPI backend.
 *
 * The backend runs on 127.0.0.1:8000 and is never deployed (it can arm the
 * unattended FTMO runner). Everything here assumes localhost; there is no auth
 * layer because there is no network exposure.
 *
 * FTMO is the only venue. The IBKR half of this client — account, positions,
 * orders, bars, symbol search and the whole preview/execute write path — was
 * removed on 2026-08-09 with the venue itself.
 *
 * Error handling is deliberate: the backend returns human-readable `detail`
 * strings written for Koko to read, and this client surfaces them verbatim
 * instead of collapsing them to "request failed". A 503/504 means "the venue
 * did not answer, state unknown", which is a different thing from an error,
 * and the UI needs to be able to tell them apart.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  status: number;
  /** True when the backend is telling us state is UNKNOWN, not absent. */
  unknownState: boolean;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.unknownState = status === 504 || status === 503;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      `Cannot reach the trading API at ${API_BASE}. Start it with ./run_web.sh (or uvicorn api.main:app).`
    );
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

/* ------------------------------------------------------------------ types */

/** An OHLCV bar. Shared by the price chart and the Kronos forecast chart. */
export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}

/**
 * A journal fill drawn on the chart.
 *
 * Which venue a row belongs to is load-bearing, not decoration: the journal
 * holds both brokers and they share ticker spellings, so an IBKR AAPL share is
 * not an FTMO AAPL CFD. The bars endpoint filters by venue before returning
 * these — without that, one venue's fills get drawn on the other's chart,
 * asserting a trade that never happened there.
 */
export interface TradeMarker {
  time: number;
  event: string;
  side: string;
  kind: "entry" | "exit";
  price: number | null;
  stop: number | null;
  quantity: number | null;
  status: string;
  detail: string;
  text: string;
}

export interface Levels {
  supports: number[];
  resistances: number[];
  week52High: number | null;
  week52Low: number | null;
  error: string | null;
}

export interface JournalSummary {
  total: number;
  byEvent: Record<string, number>;
  superseded: number;
  disputed: number;
  blocked: number;
  lastTimestamp: string | null;
  path: string;
}

/**
 * Everything the shell needs that is NOT on the FTMO socket.
 *
 * Deliberately venue-independent: it reads settings and a CSV, so it answers
 * while cTrader is unreachable. Anything needing the venue comes over
 * /ws/ftmo, which is allowed to be down.
 */
export interface Status {
  venue: "ftmo";
  signal: { active: string; default: string; disabled: string[] };
  journal: JournalSummary;
  /**
   * Whether the runner is inside its 16:30-11:30 Sofia window right now.
   * `open: null` means the window could not be evaluated — unknown, never
   * "closed".
   */
  tradingWindow: { open: boolean | null; reason: string };
  settings: {
    riskPctPerTrade: number | null;
    benchmark: string | null;
  };
}

export interface IndicatorPoint {
  time: number;
  value: number;
}

export interface IndicatorResult {
  id: string;
  key: string;
  name: string;
  pane: string;
  params: Record<string, number>;
  series: Record<string, IndicatorPoint[]>;
  bounds: [number, number] | null;
  guides: number[];
  error: string | null;
}

export interface IndicatorCatalogEntry {
  key: string;
  name: string;
  pane: string;
  params: Record<string, number>;
  outputs: string[];
  description: string;
  bounds: [number, number] | null;
  guides: number[];
}

export interface JournalRow {
  index: number;
  timestamp: string;
  epoch: number | null;
  event: string;
  symbol: string;
  secType: string;
  action: string;
  quantity: number | null;
  price: number | null;
  stop: number | null;
  target: number | null;
  status: string;
  detail: string;
  /** "ibkr" | "ftmo", or "" for rows written before the venue column existed. */
  venue: string;
  superseded: boolean;
  supersededBy: number | null;
  disputed: boolean;
  disputeNote: string;
}

export interface BacktestRow {
  ticker: string;
  period: string;
  strategy_cagr_pct: number | null;
  strategy_max_dd_pct: number | null;
  strategy_sharpe: number | null;
  strategy_trades: number | null;
  strategy_win_rate_pct: number | null;
  bh_cagr_pct: number | null;
  spy_bh_cagr_pct: number | null;
}

export interface BacktestPeriod {
  key: string;
  label: string;
  caveat: string;
  tickers: number;
  rows: BacktestRow[];
  avgStrategyCagr: number | null;
  avgBuyHoldCagr: number | null;
  avgSpyCagr: number | null;
  avgMaxDd: number | null;
  beatSpy: number;
  beatBuyHold: number;
}

/**
 * `computed: false` means the numbers are quoted from CLAUDE.md rather than
 * calculated by this API. The UI must show that distinction — mixing the two
 * is how a written-down result starts looking like a fresh measurement.
 */
export interface BacktestFinding {
  name: string;
  verdict: "beat" | "lost" | "no-edge";
  computed: boolean;
  source: string;
  metrics: { label: string; value: string }[];
  note: string;
  status?: string;
}

export interface BacktestsResponse {
  results: {
    strategy: string;
    source: string;
    periods: BacktestPeriod[];
    error: string | null;
  };
  findings: BacktestFinding[];
}

/* ----------------------------------------------------------------- calls */

export const api = {
  status: () => request<Status>("/api/status"),
  indicatorCatalog: () =>
    request<{ indicators: IndicatorCatalogEntry[] }>("/api/indicators/catalog"),
  journal: (params: { symbol?: string; event?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.symbol) q.set("symbol", params.symbol);
    if (params.event) q.set("event", params.event);
    if (params.limit) q.set("limit", String(params.limit));
    const suffix = q.toString() ? `?${q}` : "";
    return request<{ rows: JournalRow[]; summary: JournalSummary }>(
      `/api/journal${suffix}`
    );
  },
  backtests: () => request<BacktestsResponse>("/api/backtests"),
};

/* ----------------------------------------------------------------- jobs */

export interface Job<T = unknown> {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  createdAt: number;
  startedAt: number | null;
  finishedAt: number | null;
  elapsedSeconds: number;
  progress: number;
  message: string;
  params: Record<string, unknown>;
  error: string | null;
  log?: string[];
  /** Null unless status === "done". A running job has no result. */
  result: T | null;
}

export interface KronosStat {
  ticker: string;
  rank: number;
  meanReturnPct: number;
  minReturnPct: number;
  maxReturnPct: number;
  spreadPct: number;
  stdevPct: number;
  draws: number[];
}

export interface KronosChartData {
  history: Bar[];
  forecast: Bar[];
  lastClose: number;
  predictedClose: number;
}

export interface KronosResult {
  generatedAt: number;
  tickers: string[];
  requested: string[];
  skipped: string[];
  draws: number;
  sampleCount: number;
  predLen: number;
  topN: number;
  stats: KronosStat[];
  perDraw: Record<string, number>[];
  rankChangesPerDraw: number;
  boundaryGap: number | null;
  gapWarning: string | null;
  charts: Record<string, KronosChartData>;
}

export interface MonteCarloResult {
  ticker: string;
  generatedAt: number;
  paths: number;
  predLen: number;
  lastClose: number;
  history: Bar[];
  series: IndicatorPoint[][];
  finalReturnsPct: number[];
  medianReturnPct: number;
  meanReturnPct: number;
  p10ReturnPct: number;
  p90ReturnPct: number;
  shareUp: number;
}

export const kronos = {
  run: (opts: { tickers?: string[]; draws?: number; sampleCount?: number }) =>
    post<Job<KronosResult>>("/api/kronos/run", opts),
  monteCarlo: (ticker: string, paths: number) =>
    post<Job<MonteCarloResult>>("/api/kronos/montecarlo", { ticker, paths }),
  /**
   * The most recent completed run of a job kind.
   *
   * Generic in the result, because the `kind` decides what comes back:
   * "kronos" yields a `KronosResult`, "kronos-mc" a `MonteCarloResult`. It
   * used to be hardcoded to the former, which meant the Monte Carlo caller
   * had to cast through `unknown` — a cast that would have kept compiling if
   * either shape ever changed.
   */
  latest: <T = KronosResult>(kind = "kronos") =>
    request<{ job: Job<T> | null; running: Job | null }>(
      `/api/kronos/latest?kind=${encodeURIComponent(kind)}`
    ),
};

export const jobs = {
  get: <T>(id: string) => request<Job<T>>(`/api/jobs/${id}`),
  cancel: (id: string) => post<{ cancelled: string }>(`/api/jobs/${id}/cancel`, {}),
};

/* ----------------------------------------------------- FTMO Kronos plan */

export interface FtmoAutotradeState {
  enabled: boolean;
  riskPct: number;
  rotationMarginPct: number;
  topN: number;
  sampleCount: number;
  product: string;
  bufferPct: number;
  dayState: {
    ftmo_day: string;
    day_start_balance: number;
    highest_eod_balance: number;
    trading_days: number;
    daily_profits: number[];
    opened_today: boolean;
  } | null;
}

export interface FtmoRankRow {
  symbol: string;
  assetClass: string;
  predictedReturnPct: number;
  lastClose: number;
  atr: number;
}

export interface FtmoEntry {
  symbol: string;
  asset_class: string;
  side: string;
  volume: number;
  units: number;
  entry_price: number;
  stop_price: number;
  risk_at_stop: number;
  predicted_return_pct: number;
  atr: number;
}

export interface FtmoPlanResult {
  generatedAt: number;
  armed: boolean;
  sampleCount: number;
  topN: number;
  rotationMarginPct: number;
  verdict: {
    canOpen: boolean;
    mustFlatten: boolean;
    breached: boolean;
    reasons: string[];
    posture: string;
  };
  account: {
    balance: number;
    equity: number;
    dayStartBalance: number;
    unpricedPositions: number;
  };
  held: string[];
  target: string[];
  exits: string[];
  entries: FtmoEntry[];
  skipped: string[];
  rankGap: number | null;
  gapIsNarrow: boolean;
  rejectedSymbols: { symbol: string; reason: string }[];
  ranked: FtmoRankRow[];
}

/**
 * A chartable instrument. Narrower than `FtmoUniverseSymbol` on purpose: the
 * volume fields the sizer needs are only meaningful for symbols the runner is
 * configured to trade, and this list covers every symbol the venue carries.
 */
export interface FtmoSymbol {
  symbol: string;
  /** "" when the symbol is outside the configured trading universe. */
  assetClass: string;
  digits: number | null;
  quoteAsset: string;
}

export interface FtmoUniverseSymbol {
  symbol: string;
  assetClass: string;
  minVolume: number;
  stepVolume: number;
  digits: number;
  quoteAsset: string;
}

/**
 * The FTMO chart payload.
 *
 * Deliberately its own type. The retired IBKR bars response carried
 * `duration`, `fromCache`, `ageSeconds` and `stale`, which existed because that
 * broker
 * paces historical requests and is asked for a span of time. cTrader is asked
 * for a bar COUNT and has no such cache, so those fields would be invented
 * values a component could render as if they meant something.
 */
export interface FtmoBarsResponse {
  symbol: string;
  /** Same as `symbol` — a CFD's venue name is its label. */
  label: string;
  /** Asset class from the traded universe, or "" if not configured. */
  kind: string;
  /** Price decimals as the venue states them: 2 for indices, 5 for FX. */
  digits: number | null;
  period: string;
  timeframe: string;
  bars: Bar[];
  count: number;
  venue: "ftmo";
  delayed: boolean;
  indicators: IndicatorResult[];
  levels: Levels | null;
  markers: TradeMarker[];
}

export interface FtmoTimeframe {
  key: string;
  period: string;
  count: number;
}

/** One hourly wakeup slot on the night band. */
export interface FtmoSlot {
  at: string;
  label: string;
  /**
   * `ran`    — the window was open and the runner logged an evaluation
   * `forced` — a record exists but the window was CLOSED: a --force run, a
   *            --reconcile, or a plan previewed from this dashboard. Not a
   *            scheduled firing, and must never be drawn as one.
   * `missed` — the window was open and NOTHING ran. This is the sleeping Mac.
   * `closed` — outside the window, as designed.
   */
  state: "ran" | "forced" | "missed" | "closed";
  reason: string;
  entries: string[];
  exits: string[];
  firings: number;
}

export interface FtmoTimeline {
  start: string;
  end: string;
  timezone: string;
  now: string;
  slots: FtmoSlot[];
  trace: {
    at: string;
    equity: number | null;
    dailyUsed: number | null;
    drawdownUsed: number | null;
    openPositions: number | null;
    breached: boolean;
    mustFlatten: boolean;
  }[];
  limits: {
    dailySoft: number | null;
    dailyFlatten: number | null;
    dailyHard: number | null;
    drawdownSoft: number | null;
    drawdownFlatten: number | null;
    drawdownHard: number | null;
    floorEquity: number | null;
  };
  counts: { ran: number; forced: number; missed: number; closed: number };
}

export const ftmo = {
  autotrade: () => request<FtmoAutotradeState>("/api/ftmo/autotrade"),
  /** Last night's session from the audit trail. Answers with the venue down. */
  timeline: () => request<FtmoTimeline>("/api/ftmo/timeline"),
  universe: () => request<{ universe: FtmoUniverseSymbol[] }>("/api/ftmo/universe"),
  /** All chartable instruments, not just the traded universe. */
  symbols: () => request<{ symbols: FtmoSymbol[] }>("/api/ftmo/symbols"),
  timeframes: () =>
    request<{ timeframes: FtmoTimeframe[]; default: string }>(
      "/api/ftmo/timeframes"
    ),
  bars: (params: {
    symbol: string;
    timeframe?: string;
    indicators?: string[];
    levels?: boolean;
    markers?: boolean;
  }) => {
    const q = new URLSearchParams({ symbol: params.symbol });
    if (params.timeframe) q.set("period", params.timeframe);
    if (params.indicators?.length) q.set("indicators", params.indicators.join(","));
    if (params.levels) q.set("levels", "true");
    if (params.markers === false) q.set("markers", "false");
    return request<FtmoBarsResponse>(`/api/ftmo/bars?${q.toString()}`);
  },
  setAutotrade: (enabled: boolean) =>
    post<{ autotrade: FtmoAutotradeState; changed: boolean }>(
      "/api/ftmo/autotrade",
      { enabled }
    ),
  plan: (sampleCount?: number) =>
    post<Job<FtmoPlanResult>>(
      `/api/ftmo/plan${sampleCount ? `?sampleCount=${sampleCount}` : ""}`,
      {}
    ),
};
