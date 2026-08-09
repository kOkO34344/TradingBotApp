"use client";

/**
 * use-fetch.ts — the one-shot fetch hook every screen loads its data with.
 *
 * Extracted from `use-live.ts` on 2026-08-09 when IBKR was removed. That file
 * was mostly the IBKR WebSocket hub — connection state, order status, fills,
 * account values — and went with the venue; this helper never had anything to
 * do with a broker and three screens still read through it.
 *
 * IT RETAINS LAST-GOOD DATA ON ERROR, deliberately, and that is a trap the
 * caller has to handle. A dashboard that blanks during an outage is a
 * dashboard nobody can use to diagnose the outage. But it means a failed
 * fetch leaves the PREVIOUS subject's data on screen under the NEW subject's
 * heading, which asserts something false — so a screen that switches subjects
 * (the chart's symbol box) must null its own state out on error. See
 * `chartData` in `components/screens/charts-screen.tsx`.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError } from "./api";

export interface Fetched<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

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
