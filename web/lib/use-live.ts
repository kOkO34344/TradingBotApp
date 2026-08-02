"use client";

/**
 * use-live.ts — WebSocket push from the backend, plus small fetch helpers.
 *
 * The socket carries connection state, order status changes, fills, position
 * changes and account values. It reconnects on its own with backoff, and —
 * importantly — reports its own health, because a UI that silently stops
 * receiving updates looks exactly like a market where nothing is happening.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  type ConnectionState,
  type WsMessage,
  wsUrl,
} from "@/lib/api";

const RECONNECT_BACKOFF = [1000, 2000, 4000, 8000, 15000, 30000];

export interface LiveState {
  /** Browser <-> backend socket. */
  socketOpen: boolean;
  /** Backend <-> IB Gateway, as last reported over that socket. */
  connection: ConnectionState | null;
  /** Epoch ms of the last message of any kind, including keepalive pings. */
  lastMessageAt: number | null;
  /** Monotonic counters the screens subscribe to for cheap invalidation. */
  revisions: Record<string, number>;
  lastError: { code?: number; message?: string; symbol?: string } | null;
}

export function useLive(): LiveState {
  const [socketOpen, setSocketOpen] = useState(false);
  const [connection, setConnection] = useState<ConnectionState | null>(null);
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const [revisions, setRevisions] = useState<Record<string, number>>({
    orders: 0,
    positions: 0,
    account: 0,
    fills: 0,
  });
  const [lastError, setLastError] =
    useState<LiveState["lastError"]>(null);

  const attemptRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);
  const closedRef = useRef(false);
  const lastBumpRef = useRef<Record<string, number>>({});

  /**
   * Increment the revision counters the screens refetch on.
   *
   * Throttled per key. Screens refetch when a counter changes, and several
   * of those fetches make IBKR requests that can themselves generate events
   * — so an unthrottled bump can close a loop between the UI and the broker.
   * That is not hypothetical: it happened, and it fired hundreds of position
   * requests a minute. The individual event is still delivered; only the
   * "go refetch" signal is rate-limited, so at worst an update lands a
   * second late.
   */
  const bump = useCallback((...keys: string[]) => {
    const now = Date.now();
    const allowed = keys.filter((k) => now - (lastBumpRef.current[k] ?? 0) > 1000);
    if (!allowed.length) return;
    for (const k of allowed) lastBumpRef.current[k] = now;
    setRevisions((prev) => {
      const next = { ...prev };
      for (const k of allowed) next[k] = (next[k] ?? 0) + 1;
      return next;
    });
  }, []);

  useEffect(() => {
    closedRef.current = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closedRef.current) return;
      const ws = new WebSocket(wsUrl());
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setSocketOpen(true);
      };

      ws.onmessage = (event) => {
        setLastMessageAt(Date.now());
        let msg: WsMessage;
        try {
          msg = JSON.parse(event.data);
        } catch {
          return;
        }
        switch (msg.topic) {
          case "connection":
            setConnection(msg.data as ConnectionState);
            // A reconnect invalidates everything: whatever the screens are
            // showing was fetched over a socket that is no longer the one
            // in use.
            bump("orders", "positions", "account");
            break;
          case "orderStatus":
            bump("orders", "positions");
            break;
          case "execution":
            bump("fills", "orders", "positions", "account");
            break;
          case "position":
            // Deliberately does NOT bump "positions". IBKR re-emits a
            // position event for every reqPositions call, so bumping here
            // creates a closed loop: fetch positions -> IBKR echoes ->
            // revision bumps -> fetch positions. That loop hammered the
            // Gateway with hundreds of requests a minute the first time this
            // screen ran. Real position changes always arrive with an
            // execution or orderStatus event, which do bump.
            break;
          case "accountValue":
            bump("account");
            break;
          case "ibError":
            setLastError(msg.data as LiveState["lastError"]);
            break;
          default:
            break;
        }
      };

      ws.onclose = () => {
        setSocketOpen(false);
        socketRef.current = null;
        if (closedRef.current) return;
        const delay =
          RECONNECT_BACKOFF[
            Math.min(attemptRef.current, RECONNECT_BACKOFF.length - 1)
          ];
        attemptRef.current += 1;
        timer = setTimeout(connect, delay);
      };

      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closedRef.current = true;
      if (timer) clearTimeout(timer);
      socketRef.current?.close();
    };
  }, [bump]);

  return { socketOpen, connection, lastMessageAt, revisions, lastError };
}

export interface Fetched<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Fetch once, then again whenever `deps` change.
 *
 * Errors are kept alongside the last good data rather than replacing it, so
 * a transient failure shows a warning over the previous values instead of
 * blanking the screen — but the values are never silently presented as
 * current. Every caller pairs this with a visible staleness indicator.
 */
export function useFetch<T>(
  fn: () => Promise<T>,
  deps: unknown[] = []
): Fetched<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  // `fn` is a fresh closure on every render, so it is deliberately NOT a
  // dependency — `deps` is what decides when to refetch. It used to be held
  // in a ref assigned during render, which is a documented React hazard
  // (and now a lint error): a render that never commits would leave the ref
  // pointing at a fetcher from an abandoned render.
  const fnForEffect = fn;

  useEffect(() => {
    let cancelled = false;
    // Marking the request in flight is the point of this effect, not a
    // cascade: it runs once per dependency change and settles immediately.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    fnForEffect()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(
          err instanceof ApiError
            ? err
            : new ApiError(0, err instanceof Error ? err.message : String(err))
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}
