import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "antd";
import type { Page } from "@dw/contracts";

vi.mock("../auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-A" } }),
}));

import { useCachedPages } from "../use-cached-pages";

afterEach(() => vi.restoreAllMocks());

function page(items: string[], next: string | null): Page<string> {
  return { items, next_cursor: next };
}

describe("useCachedPages", () => {
  it("appends the next page instead of replacing what is shown", async () => {
    const load = vi.fn(async (cursor: string | null) =>
      cursor === null ? page(["a", "b"], "c2") : page(["c", "d"], null),
    );
    const { result } = renderHook(() => useCachedPages("k1", load), {
      wrapper: App,
    });

    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() =>
      expect(result.current.items).toEqual(["a", "b", "c", "d"]),
    );
    expect(load).toHaveBeenLastCalledWith("c2");
  });

  it("stops only on a null cursor, never on an empty page", async () => {
    // A filtered listing can hand back an empty page mid-run. Treating that as
    // the end would hide every row behind it.
    const load = vi.fn(async (cursor: string | null) => {
      if (cursor === null) return page([], "c2");
      if (cursor === "c2") return page(["late"], "c3");
      return page([], null);
    });
    const { result } = renderHook(() => useCachedPages("k2", load), {
      wrapper: App,
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual([]);
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual(["late"]));
    expect(result.current.hasMore).toBe(true);

    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.hasMore).toBe(false));
  });

  it("drops the pages it had when the first page is reloaded", async () => {
    // A reload is a new window: the old page 2 describes rows that may no
    // longer follow the new page 1, so splicing them together would show a
    // listing that never existed.
    let firstPage = page(["a"], "c2");
    const load = vi.fn(async (cursor: string | null) =>
      cursor === null ? firstPage : page(["b"], null),
    );
    const { result } = renderHook(() => useCachedPages("k3", load), {
      wrapper: App,
    });

    await waitFor(() => expect(result.current.items).toEqual(["a"]));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]));

    firstPage = page(["a2"], null);
    act(() => result.current.reload());

    await waitFor(() => expect(result.current.items).toEqual(["a2"]));
    expect(result.current.hasMore).toBe(false);
  });

  it("never asks for a page it was not handed a cursor for", async () => {
    const load = vi.fn(async () => page(["only"], null));
    const { result } = renderHook(() => useCachedPages("k4", load), {
      wrapper: App,
    });

    await waitFor(() => expect(result.current.items).toEqual(["only"]));
    expect(result.current.hasMore).toBe(false);

    act(() => result.current.loadMore());
    expect(load).toHaveBeenCalledTimes(1);
  });
});
