import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import { stateForError } from "@dw/ui";
import { toRegionError } from "../../lib/error-message";
import { LoadError } from "../load-error";

afterEach(cleanup);

function apiError(status: number, code: string, message: string) {
  return new ApiError(status, {
    code,
    message,
    details: {},
    request_id: "req-42",
  });
}

describe("one table from the server's code to a region's state (ui-quality §4)", () => {
  it.each([
    ["permission_denied", "forbidden"],
    ["entitlement_denied", "entitlement"],
    ["not_found", "notfound"],
    ["conflict", "conflict"],
    ["internal", "error"],
    ["a_code_this_build_does_not_know", "error"],
  ])("%s draws %s", (code, kind) => {
    expect(stateForError(toRegionError(apiError(400, code, "x"))).kind).toBe(
      kind,
    );
  });

  it("reads a request that never reached the server as offline", () => {
    expect(toRegionError(new TypeError("Failed to fetch"))).toBe("offline");
    expect(toRegionError(new TypeError("Load failed"))).toBe("offline");
    // A bug is not a network failure.
    expect(
      stateForError(toRegionError(new TypeError("x is not a function"))).kind,
    ).toBe("error");
  });
});

describe("LoadError", () => {
  it("shows the server's sentence, its request id and Thử lại on an error", () => {
    const retry = vi.fn();
    render(
      <LoadError
        error={apiError(500, "internal", "Máy chủ đang bận")}
        onRetry={retry}
      />,
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("Máy chủ đang bận")).toBeTruthy();
    expect(screen.getByText("req-42")).toBeTruthy();
    expect(screen.queryByText(/internal/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("says forbidden as forbidden, with no retry", () => {
    render(
      <LoadError
        error={apiError(403, "permission_denied", "x")}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Bạn không có quyền xem mục này")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Thử lại/ })).toBeNull();
  });

  it("says offline, not an error, and offers to retry", () => {
    render(
      <LoadError
        compact
        error={new TypeError("Failed to fetch")}
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Mất kết nối mạng")).toBeTruthy();
    expect(screen.queryByText(/Không tải được/)).toBeNull();
    expect(screen.getByRole("button", { name: /Thử lại/ })).toBeTruthy();
  });
});
