"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { App } from "antd";
import type { Page } from "@dw/contracts";
import { errorMessage } from "./error-message";
import { useCachedResource } from "./use-cached-resource";

/**
 * A cursor-paged list: the first page cached, the rest appended on demand.
 *
 * Every list screen used to ask for `limit: 200` and render `.items`, which is
 * not a page — it is a silent truncation. The 201st row did not exist as far as
 * the user was concerned, with nothing on screen saying so, and the number was
 * simply `MAX_PAGE_SIZE` because that was the largest the server would allow.
 *
 * The first page goes through {@link useCachedResource} so navigating away and
 * back still shows the list instantly. Pages after it live in local state and
 * are deliberately NOT cached: they are what this visit asked for, and reviving
 * them on a later visit would mean showing a deep scroll position built from
 * cursors minted against a window that has since moved.
 *
 * `next_cursor === null` is the only stop condition — never an empty `items`,
 * which a filtered listing can return mid-run with rows still behind it.
 *
 * `loadPage` must be memoized (`useCallback`) on the same inputs as `key`, for
 * the same reason `useCachedResource` requires it.
 */
export function useCachedPages<Item>(
  key: string,
  loadPage: (cursor: string | null) => Promise<Page<Item>>,
): {
  items: Item[];
  /** The first page is still on its way and there is nothing cached to show. */
  loading: boolean;
  /** A later page is on its way; the rows already shown stay put. */
  loadingMore: boolean;
  error: unknown;
  hasMore: boolean;
  loadMore: () => void;
  reload: () => void;
} {
  const first = useCachedResource<Page<Item>>(
    key,
    useCallback(() => loadPage(null), [loadPage]),
  );
  const { message } = App.useApp();
  const [extra, setExtra] = useState<Page<Item>[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);

  // A refetch of the first page is a new window: what was page 2 of the old one
  // describes rows that may no longer follow page 1. Drop them and let the user
  // ask again rather than splice two windows together.
  useEffect(() => {
    setExtra([]);
  }, [first.data]);

  // Which first page the pages in `extra` were built on. `loadMore` is async,
  // so the list can be refetched while a page is in flight; appending it then
  // would put rows from the old window behind the new one.
  const builtOn = useRef(first.data);
  builtOn.current = first.data;

  const tail = extra.length > 0 ? extra[extra.length - 1] : first.data;
  const nextCursor = tail?.next_cursor ?? null;

  const loadMore = useCallback(() => {
    if (nextCursor === null || loadingMore) return;
    const startedOn = builtOn.current;
    setLoadingMore(true);
    loadPage(nextCursor)
      .then((page) => {
        if (builtOn.current !== startedOn) return;
        setExtra((pages) => [...pages, page]);
      })
      .catch((failure: unknown) => {
        message.error(errorMessage(failure));
      })
      .finally(() => setLoadingMore(false));
  }, [nextCursor, loadingMore, loadPage, message]);

  const items = [
    ...(first.data?.items ?? []),
    ...extra.flatMap((p) => p.items),
  ];

  return {
    items,
    loading: first.loading,
    loadingMore,
    error: first.error,
    hasMore: nextCursor !== null,
    loadMore,
    reload: first.reload,
  };
}
