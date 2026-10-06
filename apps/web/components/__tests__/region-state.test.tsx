import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import { RegionState, regionKind } from "@dw/ui";
import { regionFailure } from "../../lib/error-message";

afterEach(cleanup);

function apiError(status: number, code: string, message: string) {
  return new ApiError(status, {
    code,
    message,
    details: {},
    request_id: "req-42",
  });
}

describe("one table from errorCode() to a region's state (ui-quality §4)", () => {
  it.each([
    ["permission_denied", "forbidden"],
    ["entitlement_denied", "entitlement"],
    ["not_found", "notFound"],
    ["conflict", "conflict"],
    ["internal", "error"],
  ])("%s draws %s", (code, kind) => {
    expect(regionKind(regionFailure(apiError(400, code, "x")))).toBe(kind);
  });

  it("reads a request that never reached the server as offline", () => {
    expect(regionKind(regionFailure(new TypeError("Failed to fetch")))).toBe(
      "offline",
    );
    // A bug is not a network failure.
    expect(
      regionKind(regionFailure(new TypeError("x is not a function"))),
    ).toBe("error");
  });
});

describe("RegionState", () => {
  it("shows the server's sentence, its request id and Thử lại on an error", () => {
    const retry = vi.fn();
    render(
      <RegionState
        failure={regionFailure(apiError(500, "internal", "Máy chủ đang bận"))}
        what="danh sách Hồ sơ PO"
        onRetry={retry}
      />,
    );
    expect(screen.getByText("Không tải được danh sách Hồ sơ PO")).toBeTruthy();
    expect(screen.getByText("Máy chủ đang bận")).toBeTruthy();
    expect(screen.getByText(/Mã yêu cầu: req-42/)).toBeTruthy();
    expect(screen.queryByText(/internal/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("says forbidden as forbidden, with no retry", () => {
    render(
      <RegionState
        failure={regionFailure(apiError(403, "permission_denied", "x"))}
        what="Hồ sơ PO"
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Bạn chưa được xem Hồ sơ PO")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Thử lại/ })).toBeNull();
  });

  it("says offline, not an error, and offers to retry", () => {
    render(
      <RegionState
        compact
        failure={regionFailure(new TypeError("Failed to fetch"))}
        what="chứng từ"
        onRetry={() => {}}
      />,
    );
    expect(screen.getByText("Mất kết nối mạng")).toBeTruthy();
    expect(screen.queryByText(/Không tải được/)).toBeNull();
    expect(screen.getByRole("button", { name: /Thử lại/ })).toBeTruthy();
  });

  it("draws nothing once the region is ready", () => {
    const { container } = render(<RegionState what="x" />);
    expect(container.innerHTML).toBe("");
  });
});
