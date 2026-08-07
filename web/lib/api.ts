/**
 * api.ts — typed client for the local FastAPI backend.
 *
 * The backend runs on 127.0.0.1:8000 and is never deployed (it holds a live
 * IB Gateway connection and can place orders). Everything here assumes
 * localhost; there is no auth layer because there is no network exposure.
 *
 * Error handling is deliberate: the backend returns human-readable `detail`
 * strings written for Koko to read, and this client surfaces them verbatim
 * instead of collapsing them to "request failed". A 504 on the positions
 * endpoint means "IBKR didn't answer, state unknown", which is a different
 * thing from an error, and the UI needs to be able to tell them apart.
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

/* ------------------------------------------------------------------ types */

export interface ConnectionState {
  connected: boolean;
  account: string | null;
  paper: boolean;
  host: string;
  port: number;
  clientId: number;
  connectedSince: number | null;
  error: string | null;
  attempts: number;
  marketDataType: string;
  /**
   * True when the backend was told not to dial IB Gateway at all (rule 9
   * retired the venue). Distinct from `connected: false`, which means it
   * tried and failed. Never render this state as a fault.
   */
  disabled: boolean;
}

export interface RiskLimits {
  max_order_notional_usd: number;
  max_open_positions: number;
  max_daily_loss_usd: number;
  require_stop_attached: boolean;
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

export interface Status {
  connection: ConnectionState;
  riskLimits: RiskLimits;
  signal: { active: string; default: string; disabled: string[] };
  autotrade: { enabled: boolean; signal: string; allowMomentum: boolean };
  journal: JournalSummary;
  marketOpen: boolean;
  settings: {
    riskPctPerTrade: number | null;
    momentumTopN: number | null;
    benchmark: string | null;
    ibkrPort: number | null;
  };
}

export interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
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

export interface BarsResponse {
  symbol: string;
  label: string;
  kind: string;
  timeframe: string;
  duration: string;
  bars: Bar[];
  count: number;
  source: string;
  delayed: boolean;
  fetchedAt: number;
  ageSeconds: number;
  fromCache: boolean;
  stale: boolean;
  error: string | null;
  indicators: IndicatorResult[];
  levels: Levels | null;
  markers: TradeMarker[];
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

export interface SymbolSuggestion {
  /** The exact string to send to /api/bars if this row is chosen. */
  query: string;
  symbol: string;
  label: string;
  name?: string;
  description: string;
  secType: string;
  exchange: string;
  currency: string;
  source: "watchlist" | "ibkr";
}

export interface ResolvedSymbol {
  key: string;
  kind: string;
  symbol: string;
  currency: string;
  exchange: string;
  expiry: string;
  label: string;
}

export interface StopOrder {
  qty: number;
  tif: string;
  status: string;
  /** Trigger price, added by the API for display; null if unavailable. */
  price: number | null;
}

export interface Position {
  symbol: string;
  secType: string;
  currency: string;
  position: number;
  avgCost: number;
  marketPrice: number | null;
  marketValue: number | null;
  unrealizedPnl: number | null;
  unrealizedPct: number | null;
  account: string;
  /** null means UNKNOWN — IBKR did not answer. Never render it as "no stop". */
  protected: boolean | null;
  protectionReason: string;
  stops: StopOrder[];
}

export interface PositionsResponse {
  positions: Position[];
  count: number;
  openOrdersError: string | null;
}

export interface OpenOrder {
  orderId: number;
  permId: number;
  parentId: number;
  symbol: string;
  secType: string;
  action: string;
  orderType: string;
  tif: string;
  quantity: number;
  limitPrice: number | null;
  stopPrice: number | null;
  status: string;
  filled: number;
  remaining: number;
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

export interface AccountSummary {
  account: string | null;
  paper: boolean;
  netLiquidationUsd: number | null;
  conversionError: string | null;
  baseCurrency: string | null;
  netLiquidation: Record<string, string>;
  totalCash: Record<string, string>;
  unrealizedPnl: number | null;
  realizedPnl: number | null;
  availableFunds: number | null;
  buyingPower: number | null;
  exchangeRates: Record<string, string>;
}

export interface Timeframe {
  key: string;
  barSize: string;
  defaultDuration: string;
  maxDuration: string;
  seconds: number;
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
  account: () => request<AccountSummary>("/api/account"),
  positions: () => request<PositionsResponse>("/api/positions"),
  orders: () => request<{ orders: OpenOrder[]; count: number }>("/api/orders"),
  timeframes: () =>
    request<{ timeframes: Timeframe[]; default: string }>("/api/timeframes"),
  indicatorCatalog: () =>
    request<{ indicators: IndicatorCatalogEntry[] }>("/api/indicators/catalog"),
  resolve: (q: string) =>
    request<ResolvedSymbol>(`/api/symbols/resolve?q=${encodeURIComponent(q)}`),
  searchSymbols: (q: string, limit = 12) =>
    request<{
      query: string;
      results: SymbolSuggestion[];
      brokerSearch: boolean;
      note: string | null;
    }>(`/api/symbols/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  watchlist: () =>
    request<{
      groups: { name: string; tickers: string[] }[];
      tickers: string[];
      resolved: ResolvedSymbol[];
    }>("/api/symbols/watchlist"),
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
  bars: (opts: {
    symbol: string;
    timeframe?: string;
    duration?: string;
    rth?: boolean;
    indicators?: string[];
    levels?: boolean;
    markers?: boolean;
  }) => {
    const q = new URLSearchParams({ symbol: opts.symbol });
    if (opts.timeframe) q.set("timeframe", opts.timeframe);
    if (opts.duration) q.set("duration", opts.duration);
    if (opts.rth !== undefined) q.set("rth", String(opts.rth));
    if (opts.indicators?.length) q.set("indicators", opts.indicators.join(","));
    if (opts.levels !== undefined) q.set("levels", String(opts.levels));
    if (opts.markers !== undefined) q.set("markers", String(opts.markers));
    return request<BarsResponse>(`/api/bars?${q}`);
  },
};

/* ------------------------------------------------------ write actions */

/**
 * Every write is preview -> execute(token). The execute call carries only
 * the token: the backend reads the order parameters from the stored preview,
 * so the browser cannot show one order and submit a different one.
 */
export interface TradePreview {
  token: string;
  kind: "flatten" | "reprotect" | "bracket" | "cancel";
  symbol: string;
  createdAt: number;
  expiresInSeconds: number;
  allowed: boolean;
  reason: string;
  steps: string[];
  warnings?: string[];
  ordersUnknown?: boolean;
  // flatten
  position?: number;
  action?: string;
  quantity?: number;
  estimatedPrice?: number;
  estimatedProceeds?: number;
  ordersToCancel?: {
    orderId: number;
    type: string;
    action: string;
    qty: number;
    tif: string;
    status: string;
    stopPrice: number | null;
  }[];
  // reprotect
  stopPrice?: number;
  currentPrice?: number;
  tif?: string;
  riskIfHit?: number;
  distancePct?: number;
  alreadyProtected?: boolean | null;
  existingCoverage?: string;
  // bracket
  autoQuantity?: number;
  quantitySource?: string;
  marketPrice?: number;
  entryLimit?: number;
  stopSource?: string;
  atr?: number;
  notional?: number;
  riskIfStopped?: number;
  riskPctOfEquity?: number;
  netLiquidationUsd?: number;
  parentTif?: string;
  stopTif?: string;
  // cancel
  orderId?: number;
  orderType?: string;
  isStop?: boolean;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export const trade = {
  previewFlatten: (symbol: string) =>
    post<TradePreview>("/api/trade/flatten/preview", { symbol }),
  previewReprotect: (symbol: string, stopPrice: number) =>
    post<TradePreview>("/api/trade/reprotect/preview", { symbol, stopPrice }),
  previewBracket: (opts: {
    symbol: string;
    action?: string;
    quantity?: number | null;
    stopPrice?: number | null;
  }) => post<TradePreview>("/api/trade/bracket/preview", opts),
  previewCancel: (orderId: number) =>
    post<TradePreview>("/api/trade/cancel/preview", { orderId }),

  execute: (kind: TradePreview["kind"], token: string) =>
    post<Record<string, unknown>>(`/api/trade/${kind}/execute`, { token }),
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
  latest: (kind = "kronos") =>
    request<{ job: Job<KronosResult> | null; running: Job | null }>(
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
 * Deliberately NOT `BarsResponse`. That type carries IBKR-only fields —
 * `duration`, `fromCache`, `ageSeconds`, `stale` — which exist because IBKR
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

export const ftmo = {
  autotrade: () => request<FtmoAutotradeState>("/api/ftmo/autotrade"),
  universe: () => request<{ universe: FtmoUniverseSymbol[] }>("/api/ftmo/universe"),
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

/* ------------------------------------------------------------ rebalance */

export interface RebalanceBuy {
  symbol: string;
  qty: number;
  entry: number;
  stop: number;
  price: number;
  atr: number;
}

export interface RebalanceProposal {
  jobId: string;
  createdAt: number;
  expiresInSeconds: number;
  decided: boolean;
  approved: boolean;
  decidedBy: string | null;
  signal: string;
  top: string[];
  top_n: number;
  net_liq_usd: number;
  sells: { symbol: string; quantity: number }[];
  holds: { symbol: string; quantity: number }[];
  buys: RebalanceBuy[];
  ranking: { ticker: string; value: number; inTop: boolean }[];
  rankLabel: string;
  marketOpen: boolean;
}

export const rebalance = {
  start: (dryRun = false) =>
    post<Job<unknown>>("/api/rebalance/start", { dryRun }),
  pending: (jobId?: string) =>
    request<{ pending: RebalanceProposal | null; job: Job<unknown> | null }>(
      `/api/rebalance/pending${jobId ? `?jobId=${jobId}` : ""}`
    ),
  decide: (jobId: string, approved: boolean) =>
    post<{ jobId: string; approved: boolean }>("/api/rebalance/decide", {
      jobId,
      approved,
    }),
};

/* ------------------------------------------------------------- websocket */

export type WsMessage =
  | { topic: "connection"; ts?: number; data: ConnectionState }
  | { topic: "orderStatus"; ts: number; data: Record<string, unknown> }
  | { topic: "execution"; ts: number; data: Record<string, unknown> }
  | { topic: "position"; ts: number; data: Record<string, unknown> }
  | { topic: "accountValue"; ts: number; data: Record<string, unknown> }
  | { topic: "ibError"; ts: number; data: Record<string, unknown> }
  | { topic: "ping"; ts: number; data: null };

export function wsUrl(): string {
  return `${API_BASE.replace(/^http/, "ws")}/ws`;
}
