"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import { useAuth } from "./auth/auth-context";
import { errorMessage } from "./error-message";

/**
 * A tiny in-memory cache for a page's data, shared across the app.
 *
 * A list page unmounts every time you navigate away, and the naive pattern
 * (`useState(loading=true)` + fetch on mount) then flashes a skeleton and
 * refetches from scratch on the way back — which is exactly what makes clicking
 * quickly between two list screens feel janky. This keeps the last
 * result in a module-level cache that outlives the unmount, so re-opening a list
 * shows its data at once and only revalidates in the background.
 *
 * The key must fold in EVERY input the fetch depends on (page, sort, filters),
 * so a different view is a different entry — otherwise a filtered list would be
 * served for the unfiltered one. Pair it with a `load` memoized (useCallback) on
 * those same inputs, or the effect refetches on every render.
 */
const resourceCache = new Map<string, unknown>();

export function useCachedResource<T>(
  rawKey: string,
  load: () => Promise<T>,
): { data: T | null; loading: boolean; error: unknown; reload: () => void } {
  // Fold the active workspace into every key. Without it, switching workspace
  // left the key unchanged, so the effect never refetched and one workspace's
  // list was served under the other's context (P1 2026-09-15). The prefix both
  // isolates the cache entry AND changes the effect dependency, so a switch
  // reloads with the new context. `none` covers the pre-ready render.
  const { active } = useAuth();
  // antd's `App` gives a `message` that reads the theme and the vi_VN locale.
  const { message } = App.useApp();
  const cacheKey = `${active?.workspaceId ?? "none"}::${rawKey}`;
  const cached = resourceCache.get(cacheKey) as T | undefined;
  const [data, setData] = useState<T | null>(cached ?? null);
  // Skeleton only when there is nothing cached to show yet.
  const [loading, setLoading] = useState(cached === undefined);
  const [tick, setTick] = useState(0);
  // What the last attempt failed with, so a caller can tell a 403 from an
  // outage and show the right screen instead of only a toast.
  const [error, setError] = useState<unknown>(null);
  // Keep the current key in a ref so reload() can stay a stable function —
  // safe to pass to a subscription or an effect dependency.
  const keyRef = useRef(cacheKey);
  keyRef.current = cacheKey;

  useEffect(() => {
    let cancelled = false;
    const seed = resourceCache.get(cacheKey) as T | undefined;
    setData(seed ?? null);
    setLoading(seed === undefined);
    setError(null);
    load()
      .then((result) => {
        if (cancelled) return;
        resourceCache.set(cacheKey, result);
        setData(result);
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(failure);
        // The server's sentence, never "permission_denied: …".
        if (!cancelled) message.error(errorMessage(failure));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // `load` is memoized by the caller on the same inputs as `cacheKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, cacheKey, load]);

  // reload() busts this key and refetches — used after a create/delete so the
  // list does not show its own stale cache. Stable identity via the key ref.
  const reload = useCallback(() => {
    resourceCache.delete(keyRef.current);
    setTick((value) => value + 1);
  }, []);

  return { data, loading, error, reload };
}
