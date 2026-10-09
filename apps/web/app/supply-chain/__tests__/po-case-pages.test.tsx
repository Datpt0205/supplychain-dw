import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import type {
  CaseApprovals,
  MissingUpdateStatus,
  POCase,
  POCaseDetail,
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
  listPOCaseApprovals: vi.fn(),
  listCaseTransitions: vi.fn(),
  listSupplierUpdates: vi.fn(),
  listDelayImpactAnalyses: vi.fn(),
  listCaseDocuments: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  createPO: vi.fn(),
  getPackagingDesign: vi.fn(),
  getPOCommercial: vi.fn(),
  listCaseDrafts: vi.fn(),
  listSupplierMessages: vi.fn(),
  getPurchaseOrderProposal: vi.fn(),
  getPOStepProposal: vi.fn(),
};
vi.mock("../../../lib/session", () => ({ apiClient: () => api }));

import POCasesPage from "../po-cases/page";
import POCaseWorkspacePage from "../po-cases/[id]/page";

beforeEach(() => {
  // Step 12's card loads on its own; these tests are about the rest of the page.
  api.getPackagingDesign.mockReturnValue(new Promise(() => {}));
  // So is the commercial card (ticket ai-automation/01).
  api.getPOCommercial.mockReturnValue(new Promise(() => {}));
  api.listCaseDrafts.mockReturnValue(new Promise(() => {}));
  api.listSupplierMessages.mockReturnValue(new Promise(() => {}));
  // And the PO draft card (ticket ai-automation/14).
  api.getPurchaseOrderProposal.mockReturnValue(new Promise(() => {}));
  // And the PO step card (tickets ai-automation/15-18).
  api.getPOStepProposal.mockReturnValue(new Promise(() => {}));
});

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
    order_kind: "new",
    product_dev_case_id: null,
    pic_user_id: null,
    category: null,
    ...overrides,
  };
}

const PRODUCT_CASE_ID = "77777777-7777-4777-8777-777777777777";
const PIC = "99999999-9999-4999-8999-999999999999";
const SKU_RED = "55555555-5555-4555-8555-555555555555";
const SKU_BLUE = "66666666-6666-4666-8666-666666666666";

function poCaseDetail(overrides: Partial<POCaseDetail> = {}): POCaseDetail {
  return { ...poCase(), lines: [], ...overrides };
}

/** A case ĐẶT HÀNG opened: no PO number yet, the PIC and Category carried,
 * one line per SKU, one of them still without a quantity. */
function awaitingPO(overrides: Partial<POCaseDetail> = {}): POCaseDetail {
  return poCaseDetail({
    po_reference: null,
    state: "order_requested",
    version: 1,
    product_dev_case_id: PRODUCT_CASE_ID,
    pic_user_id: PIC,
    category: "Nồi",
    lines: [
      {
        sku_id: SKU_RED,
        sku_code: "MH-0001-RED",
        variant_label: "Đỏ 24cm",
        quantity: 300,
      },
      {
        sku_id: SKU_BLUE,
        sku_code: "MH-0001-BLUE",
        variant_label: "Xanh 24cm",
        quantity: null,
      },
    ],
    ...overrides,
  });
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

  it("names a case awaiting its PO in words, still linked by id", async () => {
    fresh();
    api.listPOCases.mockResolvedValue({
      items: [poCase({ po_reference: null, state: "order_requested" })],
      next_cursor: null,
    });
    render(<POCasesPage />);

    const link = await screen.findByRole("link", { name: "Chưa có số PO" });
    expect(link.getAttribute("href")).toBe(`/supply-chain/po-cases/${CASE_ID}`);
    expect(screen.getByText("Chờ tạo PO")).toBeTruthy();
    expect(screen.getByText("Bước 10 · Giai đoạn 2")).toBeTruthy();
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
    expect(screen.getByText("Không tải được dữ liệu")).toBeTruthy();
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
    approvals = { visible: true, total: 0, items: [] },
  }: {
    sla?: SLAEvaluation;
    missing?: MissingUpdateStatus;
    updates?: SupplierUpdate[];
    approvals?: CaseApprovals;
  } = {}) {
    fresh();
    api.getSLAEvaluation.mockResolvedValue(sla);
    api.getMissingUpdateStatus.mockResolvedValue(missing);
    api.listPOCaseApprovals.mockResolvedValue(approvals);
    api.listCaseTransitions.mockResolvedValue({ items: [], next_cursor: null });
    api.listSupplierUpdates.mockResolvedValue(updates);
    api.listDelayImpactAnalyses.mockResolvedValue([]);
    api.listCaseDocuments.mockResolvedValue([]);
    api.listWorkspaceMembers.mockResolvedValue([]);
    return render(
      <App>
        <POCaseWorkspacePage />
      </App>,
    );
  }

  it("answers first whether the case can move on: step, SLA, NCC, approvals", async () => {
    api.getPOCase.mockResolvedValue(poCaseDetail());
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

  it("counts the case's pending approvals as the server filtered them", async () => {
    api.getPOCase.mockResolvedValue(poCaseDetail());
    renderDetail({
      approvals: {
        visible: true,
        total: 3,
        items: [
          {
            id: "99999999-9999-4999-8999-999999999999",
            action: "cancel",
            requested_at: "2026-10-05T02:00:00Z",
          },
        ],
      },
    });

    const strip = await screen.findByLabelText("Tóm tắt Hồ sơ PO");
    await waitFor(() => expect(strip.textContent).toContain("3 yêu cầu"));
    expect(api.listPOCaseApprovals).toHaveBeenCalledWith(CASE_ID);
    expect(screen.getByText("1 yêu cầu mới nhất trong 3")).toBeTruthy();
  });

  it("says approvals were not looked at for a caller without the inbox", async () => {
    api.getPOCase.mockResolvedValue(poCaseDetail());
    renderDetail({ approvals: { visible: false, total: 0, items: [] } });

    const strip = await screen.findByLabelText("Tóm tắt Hồ sơ PO");
    await waitFor(() => expect(strip.textContent).toContain("Không xem được"));
    expect(strip.textContent).not.toContain("Không có");
  });

  it("marks what a model read from the supplier, beside the supplier's own words", async () => {
    api.getPOCase.mockResolvedValue(poCaseDetail());
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
    expect(await screen.findByText("Không tìm thấy")).toBeTruthy();
  });

  it("shows the server's sentence and retries a failed load", async () => {
    api.getPOCase
      .mockRejectedValueOnce(serverError("Máy chủ đang bận"))
      .mockResolvedValueOnce(poCaseDetail());
    renderDetail();

    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    expect(
      await screen.findByRole("heading", { name: "PO-2026-007" }),
    ).toBeTruthy();
  });

  it("locks adding a document with the reason in words for a role without the write", async () => {
    api.getPOCase.mockResolvedValue(poCaseDetail());
    renderDetail();

    await screen.findByText("Hồ sơ chưa có chứng từ nào.");
    const upload = screen.getByRole("button", { name: /Tải lên/ });
    expect((upload as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/Chỉ người có quyền tải chứng từ lên/),
    ).toBeTruthy();
  });
});

describe("Hồ sơ PO: chờ tạo PO (bước 10)", () => {
  function renderAwaiting(detail: POCaseDetail = awaitingPO()) {
    fresh();
    api.getPOCase.mockResolvedValue(detail);
    api.getSLAEvaluation.mockResolvedValue({
      status: "not_applicable",
      milestone: null,
      entered_current_state_at: "2026-10-07T02:00:00Z",
      age_days: 0,
      threshold_days: null,
    });
    api.getMissingUpdateStatus.mockResolvedValue({
      status: "on_track",
      reference_at: "2026-10-07T02:00:00Z",
      age_days: 0,
    });
    api.listPOCaseApprovals.mockResolvedValue({
      visible: true,
      total: 0,
      items: [],
    });
    api.listCaseTransitions.mockResolvedValue({ items: [], next_cursor: null });
    api.listSupplierUpdates.mockResolvedValue([]);
    api.listDelayImpactAnalyses.mockResolvedValue([]);
    api.listCaseDocuments.mockResolvedValue([]);
    // The roster is cached per page load (lib/directory), so a PIC reads as
    // the short id the page falls back to.
    api.listWorkspaceMembers.mockResolvedValue([]);
    return render(
      <App>
        <POCaseWorkspacePage />
      </App>,
    );
  }

  it("says the PO does not exist yet, shows the order and links back to the product case", async () => {
    renderAwaiting();

    expect(
      await screen.findByRole("heading", { name: "Chưa có số PO" }),
    ).toBeTruthy();
    expect(screen.getAllByText("Chờ tạo PO").length).toBeGreaterThan(0);
    // Once as the order's kind, once as the form's default kind.
    expect(screen.getAllByText("Hàng mới")).toHaveLength(2);
    expect(screen.getByText("Nồi")).toBeTruthy();
    expect(screen.getByText(`${PIC.slice(0, 8)}…`)).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Hồ sơ phát triển sản phẩm" })
        .getAttribute("href"),
    ).toBe(`/supply-chain/product-cases/${PRODUCT_CASE_ID}`);
    // The lines: SKU, variant, quantity, and the one still open in words.
    expect(screen.getByText("MH-0001-RED")).toBeTruthy();
    expect(screen.getByText("Xanh 24cm")).toBeTruthy();
    expect(screen.getByText("Chưa có")).toBeTruthy();
  });

  it("creates the PO with its number, kind and every line's quantity", async () => {
    api.createPO.mockResolvedValue(
      awaitingPO({ po_reference: "PO-2026-100", state: "po_created" }),
    );
    renderAwaiting();

    fireEvent.change(await screen.findByLabelText("Số PO"), {
      target: { value: "PO-2026-100" },
    });
    fireEvent.change(
      screen.getByLabelText("Số lượng MH-0001-BLUE (Xanh 24cm)"),
      {
        target: { value: "120" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Tạo PO" }));

    await waitFor(() => expect(api.createPO).toHaveBeenCalled());
    const [caseId, input, key] = api.createPO.mock.calls[0]!;
    expect(caseId).toBe(CASE_ID);
    expect(input).toEqual({
      poReference: "PO-2026-100",
      // Defaults to a new product for a case out of stage 1.
      orderKind: "new",
      lines: [
        { skuId: SKU_RED, quantity: 300 },
        { skuId: SKU_BLUE, quantity: 120 },
      ],
    });
    expect(typeof key).toBe("string");
    expect(await screen.findByText("Đã tạo PO PO-2026-100.")).toBeTruthy();
  });

  it("asks for a line's quantity before sending", async () => {
    renderAwaiting();

    fireEvent.change(await screen.findByLabelText("Số PO"), {
      target: { value: "PO-2026-100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Tạo PO" }));

    expect(await screen.findByText("Nhập số lượng MH-0001-BLUE")).toBeTruthy();
    expect(api.createPO).not.toHaveBeenCalled();
  });

  it("shows a PO number taken in the company at its field", async () => {
    api.createPO.mockRejectedValue(
      new ApiError(409, {
        code: "conflict",
        message: "số PO PO-2026-007 đã có trong công ty",
        details: {
          constraint: "uq_po_cases_tenant_id_po_reference",
          po_reference: "PO-2026-007",
        },
      }),
    );
    renderAwaiting(
      awaitingPO({
        lines: [
          {
            sku_id: SKU_RED,
            sku_code: "MH-0001-RED",
            variant_label: "Đỏ 24cm",
            quantity: 300,
          },
        ],
      }),
    );

    fireEvent.change(await screen.findByLabelText("Số PO"), {
      target: { value: "PO-2026-007" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Tạo PO" }));

    const sentence = await screen.findByText(
      "số PO PO-2026-007 đã có trong công ty",
    );
    // At the field, not as a card-level alert.
    expect(sentence.closest(".ant-form-item")).toBeTruthy();
    expect(screen.queryByRole("alert", { name: /đã có/ })).toBeNull();
  });

  it("shows any other refusal with the server's sentence", async () => {
    api.createPO.mockRejectedValue(
      new ApiError(403, {
        code: "permission_denied",
        message: "Bước này cần nhiệm vụ Cung ứng",
        details: {},
      }),
    );
    renderAwaiting(awaitingPO({ lines: [] }));

    fireEvent.change(await screen.findByLabelText("Số PO"), {
      target: { value: "PO-2026-100" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Tạo PO" }));

    expect(
      await screen.findByText("Bước này cần nhiệm vụ Cung ứng"),
    ).toBeTruthy();
  });

  it("offers no PO creation once the case has its PO", async () => {
    renderAwaiting(
      awaitingPO({ po_reference: "PO-2026-100", state: "po_created" }),
    );

    expect(
      await screen.findByRole("heading", { name: "PO-2026-100" }),
    ).toBeTruthy();
    expect(screen.queryByText("Tạo PO (bước 10)")).toBeNull();
    expect(screen.queryByRole("button", { name: "Tạo PO" })).toBeNull();
    expect(screen.queryByLabelText("Số PO")).toBeNull();
  });

  it("says a case opened without stage 1 has no product case to go back to", async () => {
    renderAwaiting(
      poCaseDetail({ order_kind: "reorder", po_reference: "PO-2026-007" }),
    );

    expect(await screen.findByText("Hàng đặt lại")).toBeTruthy();
    expect(
      screen.getByText("Mở trực tiếp, không qua giai đoạn 1"),
    ).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: "Hồ sơ phát triển sản phẩm" }),
    ).toBeNull();
    expect(screen.getByText("Hồ sơ không có dòng hàng nào.")).toBeTruthy();
  });
});
