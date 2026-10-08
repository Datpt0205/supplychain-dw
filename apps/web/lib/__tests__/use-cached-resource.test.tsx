import { renderHook, waitFor } from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";

// The hook folds the active workspace into its cache key. Mock the auth hook so
// the test can move the caller between workspaces and watch the effect refetch.
let activeWorkspaceId: string | null = "ws-A";
vi.mock("../auth/auth-context", () => ({
  useAuth: () => ({
    active: activeWorkspaceId ? { workspaceId: activeWorkspaceId } : null,
  }),
}));

import { useCachedResource } from "../use-cached-resource";

afterEach(() => {
  activeWorkspaceId = "ws-A";
  vi.restoreAllMocks();
});

describe("useCachedResource — workspace isolation", () => {
  it("refetches when the active workspace changes", async () => {
    const load = vi.fn(async () => activeWorkspaceId);
    const { result, rerender } = renderHook(
      () => useCachedResource("leads:page1", load),
      { wrapper: App },
    );

    await waitFor(() => expect(result.current.data).toBe("ws-A"));
    expect(load).toHaveBeenCalledTimes(1);

    // The user switches workspace: same raw key, new context.
    activeWorkspaceId = "ws-B";
    rerender();

    await waitFor(() => expect(result.current.data).toBe("ws-B"));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not serve one workspace's cache under another", async () => {
    const load = vi.fn(async () => `data-for-${activeWorkspaceId}`);
    const first = renderHook(() => useCachedResource("accounts", load), {
      wrapper: App,
    });
    await waitFor(() =>
      expect(first.result.current.data).toBe("data-for-ws-A"),
    );
    first.unmount();

    activeWorkspaceId = "ws-B";
    const second = renderHook(() => useCachedResource("accounts", load), {
      wrapper: App,
    });
    // Must not flash ws-A's cached value; the ws-B entry is separate.
    await waitFor(() =>
      expect(second.result.current.data).toBe("data-for-ws-B"),
    );
  });
});
