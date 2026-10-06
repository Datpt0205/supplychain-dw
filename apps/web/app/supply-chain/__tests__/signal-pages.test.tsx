import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import type { AttentionItem } from "@dw/contracts";

vi.setConfig({ testTimeout: 30_000 });

let workspaceId = "";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId } }),
}));

const api = {
  listAttentionQueue: vi.fn(),
  getPortfolioSummary: vi.fn(),
  getDailyBrief: vi.fn(),
};
vi.mock("../../../lib/session", () => ({ apiClient: () => api }));

import AttentionQueuePage from "../attention-queue/page";
import ControlTowerPage from "../control-tower/page";
import DailyBriefPage from "../daily-brief/page";

afterEach(() => {
  cleanup();
  Object.values(api).forEach((mock) => mock.mockReset());
});

function fresh() {
  workspaceId = crypto.randomUUID();
}

const FORBIDDEN = new ApiError(403, {
  code: "permission_denied",
  message: "x",
  details: {},
});

function item(overrides: Partial<AttentionItem> = {}): AttentionItem {
  return {
    case: {
      id: "11111111-1111-4111-8111-111111111111",
      po_reference: "PO-2026-007",
      supplier_name: "Đông Á Inox",
      state: "waiting_payment",
      interrupted_state: null,
      created_at: "2026-10-01T02:00:00Z",
      version: 3,
    },
    sla: {
      status: "breached",
      milestone: "payment",
      entered_current_state_at: "2026-10-01T02:00:00Z",
      age_days: 9,
      threshold_days: 7,
    },
    missing_update: null,
    ...overrides,
  };
}

describe("Cần chú ý", () => {
  it("lists each flagged case with the signal that put it here", async () => {
    fresh();
    api.listAttentionQueue.mockResolvedValue([item()]);
    render(<AttentionQueuePage />);

    expect(
      await screen.findByRole("link", { name: "PO-2026-007" }),
    ).toBeTruthy();
    expect(screen.getByText("Trễ SLA")).toBeTruthy();
    expect(screen.getByText(/Mốc thanh toán · 9\s+ngày/)).toBeTruthy();
    expect(screen.getByText("Chờ thanh toán")).toBeTruthy();
  });

  it("says nothing needs attention, and what was not counted", async () => {
    fresh();
    api.listAttentionQueue.mockResolvedValue([]);
    render(<AttentionQueuePage />);
    expect(
      await screen.findByText(/Mốc SLA còn chờ xác nhận không được tính/),
    ).toBeTruthy();
  });

  it("never reads a refusal as an all-clear", async () => {
    fresh();
    api.listAttentionQueue.mockRejectedValue(FORBIDDEN);
    render(<AttentionQueuePage />);
    expect(
      await screen.findByText("Bạn chưa được xem danh sách cần chú ý"),
    ).toBeTruthy();
    expect(screen.queryByText(/không được tính/)).toBeNull();
  });
});

describe("Control Tower", () => {
  it("says nothing is running when nothing is", async () => {
    fresh();
    api.getPortfolioSummary.mockResolvedValue({
      active_case_count: 0,
      sla_breached_count: 0,
      update_overdue_count: 0,
      by_state: [],
      by_supplier: [],
    });
    render(<ControlTowerPage />);
    expect(
      await screen.findByText(/Chưa có Hồ sơ PO nào đang chạy/),
    ).toBeTruthy();
  });

  it("shows the server's sentence on a failed load, never an empty portfolio", async () => {
    fresh();
    api.getPortfolioSummary.mockRejectedValue(
      new ApiError(500, {
        code: "internal",
        message: "Máy chủ đang bận",
        details: {},
      }),
    );
    render(<ControlTowerPage />);
    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    expect(screen.queryByText(/Chưa có Hồ sơ PO nào đang chạy/)).toBeNull();
  });
});

describe("Bản tin hôm nay", () => {
  it("never reads a refusal as nothing to handle", async () => {
    fresh();
    api.getDailyBrief.mockRejectedValue(FORBIDDEN);
    render(<DailyBriefPage />);
    expect(await screen.findByText("Bạn chưa được xem bản tin")).toBeTruthy();
    expect(screen.queryByText(/Không có việc nào cần xử lý/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Tóm tắt bằng AI" }),
    ).toBeNull();
  });
});
