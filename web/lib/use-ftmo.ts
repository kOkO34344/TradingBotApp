"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "./api";

export interface FtmoLimit {
  used: number;
  soft: number;
  flatten: number;
  hard: number;
  floorEquity?: number;
}

export interface FtmoVerdict {
  canOpen: boolean;
  mustFlatten: boolean;
  breached: boolean;
  reasons: string[];
  posture: "OK" | "BLOCKED" | "FLATTEN" | "BREACHED";
  daily: FtmoLimit;
  drawdown: FtmoLimit;
  profit: {
    usd: number;
    targetUsd: number;
    targetReached: boolean;
    minDaysMet: boolean;
    consistencyOk: boolean;
    canPass: boolean;
  };
}

export interface FtmoPosition {
  positionId: number;
  symbol: string;
  side: "BUY" | "SELL";
  volume: number;
  units: number;
  entryPrice: number;
  stopLoss: number | null;
  protected: boolean;
  mark: number | null;
  pnl: number | null;
  quoteAgeS: number | null;
}

export interface FtmoSnapshot {
  connection: {
    venue: string;
    status: string;
    error: string | null;
    account_id: number | null;
    ready: boolean;
  };
  account: {
    accountId: number;
    balance: number;
    equity: number;
    floating: number;
    unpricedPositions: number;
  } | null;
  verdict: FtmoVerdict | null;
  positions: FtmoPosition[];
}

export interface FtmoStream {
  snap: FtmoSnapshot | null;
  /** Milliseconds since the last frame, or null before the first one. */
  ageMs: number | null;
  /** True once a frame has arrived AND it is recent enough to believe. */
  live: boolean;
}

/**
 * Subscribe to /ws/ftmo.
 *
 * `snap` is deliberately left as the LAST GOOD frame when the socket drops,
 * rather than nulled — but `live` goes false and the UI must show that. Those
 * are different claims: "here is what was true 40 seconds ago" is useful,
 * while "here is what is true now" would be a lie. Blanking the screen on a
 * blip is its own failure, because a dashboard that goes empty during an
 * outage is a dashboard nobody can use to diagnose the outage.
 *
 * Staleness is judged on wall-clock arrival, not on a flag from the server:
 * a server that has stopped sending cannot tell you it has stopped sending.
 */
export function useFtmoStream(staleAfterMs = 5000): FtmoStream {
  const [snap, setSnap] = useState<FtmoSnapshot | null>(null);
  const [lastAt, setLastAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (closed) return;
      const url = API_BASE.replace(/^http/, "ws") + "/ws/ftmo";
      let ws: WebSocket;
      try {
        ws = new WebSocket(url);
      } catch {
        retry = setTimeout(connect, 2000);
        return;
      }
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.topic === "ftmo") {
            setSnap(msg.data as FtmoSnapshot);
            setLastAt(Date.now());
          }
        } catch {
          /* a malformed frame is dropped, not rendered */
        }
      };
      // Reconnect on both close and error. An error does not always fire a
      // close, and a socket that is gone with no retry looks exactly like a
      // quiet market.
      ws.onclose = () => {
        if (!closed) retry = setTimeout(connect, 2000);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    const tick = setInterval(() => setNow(Date.now()), 500);
    return () => {
      closed = true;
      clearInterval(tick);
      if (retry) clearTimeout(retry);
      wsRef.current?.close();
    };
  }, []);

  const ageMs = lastAt === null ? null : now - lastAt;
  return { snap, ageMs, live: ageMs !== null && ageMs < staleAfterMs };
}
