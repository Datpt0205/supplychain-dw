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
    case_kind: "po",
    case_id: "c-1",
    reference: "PO-2026-007",
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
        reference: "PO-2026-008",
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

  it("names a case without its PO number in words", async () => {
    listFollowUps.mockResolvedValue([followUp({ reference: null })]);
    render(<FollowUpsPage />);

    expect(
      (await screen.findByRole("link", { name: "Chưa có số PO" })).getAttribute(
        "href",
      ),
    ).toBe("/supply-chain/po-cases/c-1");
    expect(
      screen.getByRole("textbox", { name: "Ghi chú cho Chưa có số PO" }),
    ).toBeTruthy();
  });

  it("links a product case's follow-up to its page, by its code", async () => {
    listFollowUps.mockResolvedValue([
      followUp({
        case_kind: "product",
        case_id: "p-1",
        reference: "DX-7",
        supplier_name: null,
        kind: "sla_breach",
        milestone: "bm04",
        days: 5,
        limit_days: 4,
      }),
    ]);
    render(<FollowUpsPage />);

    expect(
      (await screen.findByRole("link", { name: "DX-7" })).getAttribute("href"),
    ).toBe("/supply-chain/product-cases/p-1");
    expect(screen.getByText("5 ngày ở bước BM04, hạn 4 ngày")).toBeTruthy();
    expect(
      screen.getByRole("textbox", {
        name: "Ghi chú cho Hồ sơ phát triển DX-7",
      }),
    ).toBeTruthy();
  });

  it("never reads a failed load as nothing to do", async () => {
    listFollowUps.mockRejectedValue(new Error("403"));
    render(<FollowUpsPage />);

    expect(await screen.findByText("Không tải được dữ liệu")).toBeTruthy();
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
