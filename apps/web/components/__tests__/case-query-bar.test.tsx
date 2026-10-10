import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import type { AIWorkResponse } from "@dw/contracts";

let workspaceId = "ws-A";
vi.mock("../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId } }),
}));

const askCaseQuery = vi.fn();
vi.mock("../../lib/session", () => ({
  apiClient: () => ({ askCaseQuery }),
}));

import { CaseQueryBar } from "../supply-chain/case-query-bar";

const NOT_FOUND: AIWorkResponse = {
  intent: "list_cases",
  outcome: "supplier_not_found",
  understood: {
    state: null,
    supplier_name: null,
    active_only: false,
    po_reference: null,
    product_state: null,
    category: null,
    pic_user_id: null,
    proposal_code: null,
  },
  citations: [{ field: "supplier", quote: "Toshiba" }],
  ignored_fields: [],
  unusable_fields: [],
  candidates: [],
  data_view: null,
};

afterEach(() => {
  // No vitest globals here, so testing-library cannot clean up on its own.
  cleanup();
  askCaseQuery.mockReset();
  workspaceId = "ws-A";
});

function ask(question: string): void {
  fireEvent.change(
    screen.getByRole("textbox", {
      name: "Hỏi về PO case hoặc hồ sơ phát triển",
    }),
    {
      target: { value: question },
    },
  );
  fireEvent.submit(screen.getByRole("search"));
}

describe("CaseQueryBar", () => {
  it("asks once, with the question trimmed, and shows the answer", async () => {
    askCaseQuery.mockResolvedValue(NOT_FOUND);
    render(<CaseQueryBar />);

    ask("  PO của Toshiba  ");

    expect(
      await screen.findByText("Không tìm thấy nhà cung cấp “Toshiba”."),
    ).toBeTruthy();
    expect(askCaseQuery).toHaveBeenCalledTimes(1);
    expect(askCaseQuery.mock.calls[0]?.[0]).toBe("PO của Toshiba");
  });

  it("does not ask an empty question", () => {
    render(<CaseQueryBar />);
    ask("   ");
    expect(askCaseQuery).not.toHaveBeenCalled();
  });

  it("shows the server's own sentence when the call fails, not its code", async () => {
    askCaseQuery.mockRejectedValue(
      new ApiError(403, {
        code: "permission_denied",
        message: "Bạn không có quyền xem PO case",
        details: {},
      }),
    );
    render(<CaseQueryBar />);

    ask("PO nào trễ?");

    expect(
      await screen.findByText("Bạn không có quyền xem PO case"),
    ).toBeTruthy();
    expect(screen.queryByText(/permission_denied/)).toBeNull();
  });

  it("drops the answer when the workspace changes", async () => {
    askCaseQuery.mockResolvedValue(NOT_FOUND);
    const { rerender } = render(<CaseQueryBar />);
    ask("PO của Toshiba");
    await screen.findByText("Không tìm thấy nhà cung cấp “Toshiba”.");

    workspaceId = "ws-B";
    rerender(<CaseQueryBar />);

    await waitFor(() =>
      expect(
        screen.queryByText("Không tìm thấy nhà cung cấp “Toshiba”."),
      ).toBeNull(),
    );
  });
  it("never shows an answer that arrives after the workspace changed", async () => {
    let finish: (value: AIWorkResponse) => void = () => {};
    askCaseQuery.mockImplementation(
      () => new Promise<AIWorkResponse>((resolve) => (finish = resolve)),
    );
    const { rerender } = render(<CaseQueryBar />);
    ask("PO của Toshiba");

    workspaceId = "ws-B";
    rerender(<CaseQueryBar />);
    await act(async () => finish(NOT_FOUND));

    expect(
      screen.queryByText("Không tìm thấy nhà cung cấp “Toshiba”."),
    ).toBeNull();
  });

  it("never lets an older answer overwrite a newer one", async () => {
    const pending: ((value: AIWorkResponse) => void)[] = [];
    askCaseQuery.mockImplementation(
      () => new Promise<AIWorkResponse>((resolve) => pending.push(resolve)),
    );
    render(<CaseQueryBar />);
    ask("PO của Toshiba");
    ask("PO của Sunhouse");

    await act(async () =>
      pending[1]?.({
        ...NOT_FOUND,
        citations: [{ field: "supplier", quote: "Sunhouse" }],
      }),
    );
    await act(async () => pending[0]?.(NOT_FOUND));

    expect(
      screen.getByText("Không tìm thấy nhà cung cấp “Sunhouse”."),
    ).toBeTruthy();
    expect(
      screen.queryByText("Không tìm thấy nhà cung cấp “Toshiba”."),
    ).toBeNull();
  });

  it("announces the answer in a live region", async () => {
    askCaseQuery.mockResolvedValue(NOT_FOUND);
    render(<CaseQueryBar />);
    ask("PO của Toshiba");
    const status = screen.getByRole("status");
    await waitFor(() =>
      expect(status.textContent).toContain(
        "Không tìm thấy nhà cung cấp “Toshiba”.",
      ),
    );
  });
  it("announces a failure as an alert", async () => {
    askCaseQuery.mockRejectedValue(
      new Error("Máy chủ tạm thời không phản hồi"),
    );
    render(<CaseQueryBar />);
    ask("PO nào trễ?");
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Máy chủ tạm thời không phản hồi",
    );
  });

  it("shows no error for a request it cancelled itself", async () => {
    // What the real client does: an aborted fetch rejects with an AbortError.
    const pending: ((value: AIWorkResponse) => void)[] = [];
    askCaseQuery.mockImplementation(
      (_question: string, signal: AbortSignal) =>
        new Promise<AIWorkResponse>((resolve, reject) => {
          pending.push(resolve);
          signal.addEventListener("abort", () =>
            reject(
              new DOMException("The operation was aborted.", "AbortError"),
            ),
          );
        }),
    );
    render(<CaseQueryBar />);
    ask("PO của Toshiba");
    ask("PO của Sunhouse");
    await act(async () =>
      pending[1]?.({
        ...NOT_FOUND,
        citations: [{ field: "supplier", quote: "Sunhouse" }],
      }),
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(
      screen.getByText("Không tìm thấy nhà cung cấp “Sunhouse”."),
    ).toBeTruthy();
  });
});
