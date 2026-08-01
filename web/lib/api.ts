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
