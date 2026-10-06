import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import type {
  MissingUpdateStatus,
  POCase,
  SLAEvaluation,
  SupplierUpdate,
} from "@dw/contracts";

// antd's Table renders slowly under jsdom on Windows.
vi.setConfig({ testTimeout: 30_000 });

const CASE_ID = "11111111-1111-4111-8111-111111111111";
// The cache behind each region is keyed by workspace: one per test.
let workspaceId = "";
let scopes = new Set<string>();
let search = new URLSearchParams();

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId },
    hasScope: (scope: string) => scopes.has(scope),
  }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => search,
  useParams: () => ({ id: CASE_ID }),
}));

const api = {
  listPOCases: vi.fn(),
  getPOCase: vi.fn(),
  getSLAEvaluation: vi.fn(),
  getMissingUpdateStatus: vi.fn(),
  listApprovals: vi.fn(),
  listCaseTransitions: vi.fn(),
  listSupplierUpdates: vi.fn(),
  listDelayImpactAnalyses: vi.fn(),
  listCaseDocuments: vi.fn(),
};
vi.mock("../../../lib/session", () => ({ apiClient: () => api }));

import POCasesPage from "../po-cases/page";
import POCaseWorkspacePage from "../po-cases/[id]/page";

afterEach(() => {
  cleanup();
  Object.values(api).forEach((mock) => mock.mockReset());
  search = new URLSearchParams();
  scopes = new Set();
});

function fresh() {
  workspaceId = crypto.randomUUID();
}

function poCase(overrides: Partial<POCase> = {}): POCase {
  return {
    id: CASE_ID,
    po_reference: "PO-2026-007",
    supplier_name: "Đông Á Inox",
    state: "waiting_deposit",
    interrupted_state: null,
    created_at: "2026-10-01T02:00:00Z",
    version: 3,
    ...overrides,
  };
}

function serverError(message: string) {
  return new ApiError(500, { code: "internal", message, details: {} });
}

describe("Hồ sơ PO: danh sách", () => {
  it("lists a case with its mono number, NCC, state and step", async () => {
    fresh();
    api.listPOCases.mockResolvedValue({ items: [poCase()], next_cursor: null });
    render(<POCasesPage />);

    const link = await screen.findByRole("link", { name: "PO-2026-007" });
    expect(link.getAttribute("href")).toBe(`/supply-chain/po-cases/${CASE_ID}`);
    expect(screen.getByText("Đông Á Inox")).toBeTruthy();
    expect(screen.getByText("Chờ đặt cọc")).toBeTruthy();
    expect(screen.getByText("Bước 11 · Giai đoạn 2")).toBeTruthy();
    expect(screen.getByText("Đã tải tất cả 1 hồ sơ.")).toBeTruthy();
  });

  it("teaches where cases come from when there are none yet", async () => {
    fresh();
    api.listPOCases.mockResolvedValue({ items: [], next_cursor: null });
    render(<POCasesPage />);

    expect(
      await screen.findByText(/Chưa có Hồ sơ PO nào. Hồ sơ PO mở ở bước 10/),
    ).toBeTruthy();
  });

  it("shows the server's sentence on a failed load, never an empty list", async () => {
    fresh();
    api.listPOCases.mockRejectedValue(serverError("Máy chủ đang bận"));
    render(<POCasesPage />);

    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    expect(screen.getByText("Không tải được danh sách Hồ sơ PO")).toBeTruthy();
    expect(screen.queryByText(/Chưa có Hồ sơ PO nào/)).toBeNull();
    expect(screen.getByRole("button", { name: /Thử lại/ })).toBeTruthy();
  });

  it("finds a supplier typed without marks among the loaded cases", async () => {
    fresh();
    api.listPOCases.mockResolvedValue({
      items: [
        poCase(),
        poCase({
          id: "22222222-2222-4222-8222-222222222222",
          po_reference: "PO-2026-008",
          supplier_name: "Sunhouse",
        }),
      ],
      next_cursor: null,
    });
    render(<POCasesPage />);
    await screen.findByRole("link", { name: "PO-2026-008" });

    fireEvent.change(
      screen.getByRole("searchbox", { name: "Tìm theo số PO hoặc NCC" }),
      { target: { value: "dong a" } },
    );
    expect(screen.getByRole("link", { name: "PO-2026-007" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "PO-2026-008" })).toBeNull();
  });
});

describe("Hồ sơ PO: chi tiết", () => {
  function renderDetail({
    sla = {
      status: "breached",
      milestone: "deposit",
      entered_current_state_at: "2026-10-01T02:00:00Z",
      age_days: 5,
      threshold_days: 3,
    },
    missing = {
      status: "reminder_due",
      reference_at: "2026-10-04T02:00:00Z",
      age_days: 2,
    },
    updates = [],
  }: {
    sla?: SLAEvaluation;
    missing?: MissingUpdateStatus;
    updates?: SupplierUpdate[];
  } = {}) {
    fresh();
    api.getSLAEvaluation.mockResolvedValue(sla);
    api.getMissingUpdateStatus.mockResolvedValue(missing);
    api.listApprovals.mockResolvedValue({ items: [], next_cursor: null });
    api.listCaseTransitions.mockResolvedValue([]);
    api.listSupplierUpdates.mockResolvedValue(updates);
    api.listDelayImpactAnalyses.mockResolvedValue([]);
    api.listCaseDocuments.mockResolvedValue([]);
    return render(
      <App>
        <POCaseWorkspacePage />
      </App>,
    );
  }

  it("answers first whether the case can move on: step, SLA, NCC, approvals", async () => {
    api.getPOCase.mockResolvedValue(poCase());
    renderDetail();

    expect(
      await screen.findByRole("heading", { name: "PO-2026-007" }),
    ).toBeTruthy();
    const strip = screen.getByLabelText("Tóm tắt Hồ sơ PO");
    expect(strip.textContent).toContain("Bước 11 · Giai đoạn 2");
    await waitFor(() =>
      expect(strip.textContent).toContain("Mốc đặt cọc · 5 ngày / hạn 3 ngày"),
    );
    expect(strip.textContent).toContain("Cần nhắc NCC");
    expect(strip.textContent).toContain("Không có");
  });

  it("marks what a model read from the supplier, beside the supplier's own words", async () => {
    api.getPOCase.mockResolvedValue(poCase());
    renderDetail({
      updates: [
        {
          id: "33333333-3333-4333-8333-333333333333",
          po_case_id: CASE_ID,
          raw_text: "Hàng trễ 5 ngày do thiếu nguyên liệu",
          event_type: "production_delay",
          affected_po: "PO-2026-007",
          delay_days: 5,
          reason: "thiếu nguyên liệu",
          proposed_action: "",
          confidence: 0.6,
          source_ref: "trễ 5 ngày",
          requires_confirmation: true,
          created_at: "2026-10-05T02:00:00Z",
        },
      ],
    });

    expect(await screen.findByText("Trễ sản xuất")).toBeTruthy();
    expect(screen.getByText("Máy đọc")).toBeTruthy();
    expect(screen.getByText("Cần người xác nhận")).toBeTruthy();
    expect(screen.getByText(/“trễ 5 ngày”/)).toBeTruthy();
  });

  it("answers not found for a case outside the workspace", async () => {
    api.getPOCase.mockRejectedValue(
      new ApiError(404, { code: "not_found", message: "x", details: {} }),
    );
    renderDetail();
    expect(await screen.findByText("Không tìm thấy Hồ sơ PO")).toBeTruthy();
  });

  it("shows the server's sentence and retries a failed load", async () => {
    api.getPOCase
      .mockRejectedValueOnce(serverError("Máy chủ đang bận"))
      .mockResolvedValueOnce(poCase());
    renderDetail();

    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(
      await screen.findByRole("heading", { name: "PO-2026-007" }),
    ).toBeTruthy();
  });

  it("locks adding a document with the reason in words for a role without the write", async () => {
    api.getPOCase.mockResolvedValue(poCase());
    renderDetail();

    await screen.findByText("Hồ sơ chưa có chứng từ nào.");
    const upload = screen.getByRole("button", { name: /Tải lên/ });
    expect((upload as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/Chỉ người có quyền tải chứng từ lên/),
    ).toBeTruthy();
  });
});
