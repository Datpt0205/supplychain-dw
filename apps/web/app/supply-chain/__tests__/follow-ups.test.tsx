import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FollowUp } from "@dw/contracts";

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-A" } }),
}));

const listFollowUps = vi.fn();
const closeFollowUp = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ listFollowUps, closeFollowUp }),
}));

import FollowUpsPage from "../follow-ups/page";

function followUp(overrides: Partial<FollowUp>): FollowUp {
  return {
    id: "f-1",
    po_case_id: "c-1",
    po_reference: "PO-2026-007",
    supplier_name: "Kangaroo",
    kind: "update_reminder",
    milestone: null,
    days: 1,
    limit_days: null,
    opened_at: "2026-09-28T08:00:00Z",
    notified_at: "2026-09-28T08:00:05Z",
    mine: true,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  listFollowUps.mockReset();
  closeFollowUp.mockReset();
});

describe("Việc cần làm", () => {
  it("shows the caller's own by default and everything on request", async () => {
    listFollowUps.mockResolvedValue([
      followUp({}),
      followUp({
        id: "f-2",
        po_reference: "PO-2026-008",
        kind: "sla_breach",
        milestone: "deposit",
        days: 3,
        limit_days: 1,
        mine: false,
      }),
    ]);
    render(<FollowUpsPage />);

    expect(await screen.findByText("PO-2026-007")).toBeTruthy();
    expect(screen.queryByText("PO-2026-008")).toBeNull();

    fireEvent.click(screen.getByRole("radio", { name: "Tất cả (2)" }));
    expect(await screen.findByText("PO-2026-008")).toBeTruthy();
    expect(screen.getByText("3 ngày ở bước đặt cọc, hạn 1 ngày")).toBeTruthy();
    // Only the caller's own can be closed from here.
    expect(screen.getAllByRole("button", { name: "Đã xử lý" })).toHaveLength(1);
  });

  it("closes one with its note and reloads the list", async () => {
    listFollowUps.mockResolvedValueOnce([followUp({})]).mockResolvedValue([]);
    closeFollowUp.mockResolvedValue(undefined);
    render(<FollowUpsPage />);

    fireEvent.change(
      await screen.findByRole("textbox", { name: "Ghi chú cho PO-2026-007" }),
      { target: { value: "Đã gọi NCC" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Đã xử lý" }));

    await waitFor(() =>
      expect(closeFollowUp).toHaveBeenCalledWith("f-1", "Đã gọi NCC"),
    );
    expect(await screen.findByText("Không có việc nào")).toBeTruthy();
  });

  it("never reads a failed load as nothing to do", async () => {
    listFollowUps.mockRejectedValue(new Error("403"));
    render(<FollowUpsPage />);

    expect(await screen.findByText(/Không tải được việc cần làm/)).toBeTruthy();
    expect(screen.queryByText("Không có việc nào")).toBeNull();
  });

  it("locks closing while offline, with the reason in words", async () => {
    const online = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    listFollowUps.mockResolvedValue([followUp({})]);
    render(<FollowUpsPage />);

    const close = await screen.findByRole("button", { name: "Đã xử lý" });
    expect((close as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Không có kết nối mạng. Kết nối lại rồi thử lại."),
    ).toBeTruthy();
    fireEvent.click(close);
    expect(closeFollowUp).not.toHaveBeenCalled();
    online.mockRestore();
  });
});
