import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// antd's Table, Select and Modal render slowly under jsdom on Windows; the
// default 5 s budget measured too tight for a whole page.
vi.setConfig({ testTimeout: 30_000 });
import {
  ApiError,
  type CaseDocument,
  type ProductActionOption,
  type ProductCase,
  type ProductCaseDetail,
} from "@dw/api-client";

const ME = "99999999-9999-4999-8999-999999999999";
const CASE_ID = "11111111-1111-4111-8111-111111111111";
let scopes = new Set<string>();
let search = new URLSearchParams();

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId: "ws-A" },
    principalId: ME,
    hasScope: (scope: string) => scopes.has(scope),
  }),
}));
vi.mock("../../../lib/directory", () => ({
  useWorkspaceMembers: () => [],
  memberName: (_: unknown, id: string | null) =>
    id ? `người ${id.slice(0, 4)}` : null,
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useSearchParams: () => search,
  useRouter: () => ({ push }),
  useParams: () => ({ id: CASE_ID }),
}));

const listProductCases = vi.fn();
const getProductActionDuties = vi.fn();
const proposeProductCase = vi.fn();
const getProductCase = vi.fn();
const listProductCaseTransitions = vi.fn();
const takeProductCaseStep = vi.fn();
const listProductCaseDocuments = vi.fn();
const uploadProductCaseDocument = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    listProductCases,
    getProductActionDuties,
    proposeProductCase,
    getProductCase,
    listProductCaseTransitions,
    takeProductCaseStep,
    listProductCaseDocuments,
    uploadProductCaseDocument,
  }),
}));

import ProductCasesPage from "../product-cases/page";
import ProductCasePage from "../product-cases/[id]/page";

const ORDERING = "supply_chain.duty.ordering";
const RND = "supply_chain.duty.rnd";
const EXCEPTIONS = "supply_chain.duty.exceptions";
// Opening a case (lead decision 9); proposing also needs the ordering duty.
const PRODUCT_CASE_WRITE = "supply_chain.product_case.write";

function productCase(overrides: Partial<ProductCase> = {}): ProductCase {
  return {
    id: CASE_ID,
    proposal_code: "DX-2026-001",
    product_name: "Nồi inox 3 đáy 24cm",
    category: "Nồi",
    supplier_name: null,
    pic_user_id: ME,
    state: "proposed",
    interrupted_state: null,
    sample_round: 0,
    created_by: ME,
    created_at: "2026-10-05T02:00:00Z",
    version: 1,
    ...overrides,
  };
}

function option(
  action: ProductActionOption["action"],
  overrides: Partial<ProductActionOption> = {},
): ProductActionOption {
  return {
    action,
    required_scope: EXCEPTIONS,
    reason_required: false,
    takes_supplier: false,
    document_type: null,
    document_required: false,
    ...overrides,
  };
}

const TESTING_ACTIONS: ProductActionOption[] = [
  option("pass_sample", {
    required_scope: RND,
    document_type: "sample_evaluation",
    document_required: true,
  }),
  option("request_revision", {
    required_scope: RND,
    reason_required: true,
    document_type: "sample_revision_request",
    document_required: true,
  }),
  option("cancel", { required_scope: ORDERING, reason_required: true }),
];

function detail(overrides: Partial<ProductCaseDetail> = {}): ProductCaseDetail {
  return {
    ...productCase({
      state: "sample_testing",
      sample_round: 1,
      supplier_name: "NCC Minh Long",
    }),
    rounds: [
      {
        round_no: 1,
        opened_at: "2026-10-05T03:00:00Z",
        opened_by: ME,
        result: null,
        evaluation_document_id: null,
        closed_at: null,
        closed_by: null,
        revision_document_id: null,
        requested_changes: null,
      },
    ],
    actions: TESTING_ACTIONS,
    pending_review: null,
    ...overrides,
  };
}

function evaluation(overrides: Partial<CaseDocument> = {}): CaseDocument {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    case_kind: "product",
    case_id: CASE_ID,
    doc_type: "sample_evaluation",
    filename: "bien-ban-vong-1.pdf",
    content_type: "application/pdf",
    size_bytes: 2048,
    sha256: "a".repeat(64),
    version: 1,
    uploaded_by: ME,
    uploaded_at: "2026-10-05T04:00:00Z",
    ...overrides,
  };
}

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(() => {
  cleanup();
  scopes = new Set();
  search = new URLSearchParams();
  for (const mock of [
    listProductCases,
    getProductActionDuties,
    proposeProductCase,
    getProductCase,
    listProductCaseTransitions,
    takeProductCaseStep,
    listProductCaseDocuments,
    uploadProductCaseDocument,
    push,
  ]) {
    mock.mockReset();
  }
});

function renderList() {
  getProductActionDuties.mockResolvedValue({
    schema_version: "1.0",
    policy_id: "supply_chain_product_action_duties",
    policy_version: "1.0.0",
    action_duties: { propose: "ordering" },
  });
  return render(
    <App>
      <ProductCasesPage />
    </App>,
  );
}

function renderDetail() {
  listProductCaseTransitions.mockResolvedValue([]);
  listProductCaseDocuments.mockResolvedValue([]);
  return render(
    <App>
      <ProductCasePage />
    </App>,
  );
}

describe("Hồ sơ phát triển sản phẩm: danh sách", () => {
  it("teaches the first step when there is nothing yet", async () => {
    scopes = new Set([ORDERING]);
    listProductCases.mockResolvedValue({ items: [], next_cursor: null });
    renderList();

    expect(
      await screen.findByText(/Chưa có hồ sơ phát triển sản phẩm nào/),
    ).toBeTruthy();
    expect(listProductCases).toHaveBeenCalledWith({
      state: undefined,
      picUserId: undefined,
    });
  });

  it("names the filter when nothing matches and offers to clear it", async () => {
    search = new URLSearchParams("state=blocked&mine=1");
    listProductCases.mockResolvedValue({ items: [], next_cursor: null });
    renderList();

    expect(
      await screen.findByText("Không có hồ sơ nào khớp bộ lọc."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Xóa bộ lọc" })).toBeTruthy();
    // "Mine" narrows by the signed-in person as PIC.
    expect(listProductCases).toHaveBeenCalledWith({
      state: "blocked",
      picUserId: ME,
    });
  });

  it("shows the server's sentence on a failed load, and retries", async () => {
    listProductCases.mockRejectedValueOnce(
      new ApiError(500, {
        code: "internal",
        message: "Máy chủ đang bận",
        details: {},
      }),
    );
    listProductCases.mockResolvedValueOnce({ items: [], next_cursor: null });
    renderList();

    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    await waitFor(() => expect(listProductCases).toHaveBeenCalledTimes(2));
  });

  it("draws forbidden as forbidden", async () => {
    listProductCases.mockRejectedValue(
      new ApiError(403, {
        code: "permission_denied",
        message: "x",
        details: {},
      }),
    );
    renderList();
    expect(
      await screen.findByText("Bạn chưa được xem hồ sơ phát triển sản phẩm"),
    ).toBeTruthy();
  });

  it("lists cases with the glossary's state label and the PIC", async () => {
    listProductCases.mockResolvedValue({
      items: [productCase({ state: "sample_testing", sample_round: 2 })],
      next_cursor: null,
    });
    renderList();

    const link = await screen.findByRole("link", { name: "DX-2026-001" });
    expect(link.getAttribute("href")).toBe(
      `/supply-chain/product-cases/${CASE_ID}`,
    );
    expect(screen.getByText("Đang test mẫu")).toBeTruthy();
    expect(screen.getByText("Bạn")).toBeTruthy();
    expect(screen.getByText("Đã hiện tất cả 1 hồ sơ.")).toBeTruthy();
  });

  it("locks proposing with the reason for a role without the duty", async () => {
    scopes = new Set([RND, PRODUCT_CASE_WRITE]);
    listProductCases.mockResolvedValue({ items: [], next_cursor: null });
    renderList();

    const reason = await screen.findByText(/cần nhiệm vụ Cung ứng/);
    expect(reason).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: /Đề xuất sản phẩm/ });
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("locks proposing with the reason for a role with the duty and without the write", async () => {
    scopes = new Set([ORDERING]);
    listProductCases.mockResolvedValue({ items: [], next_cursor: null });
    renderList();

    const reason = await screen.findByText(
      /cần quyền mở hồ sơ phát triển sản phẩm/,
    );
    expect(reason).toBeTruthy();
    const buttons = screen.getAllByRole("button", { name: /Đề xuất sản phẩm/ });
    expect((buttons[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("proposes with no PIC field and opens the new case", async () => {
    scopes = new Set([ORDERING, PRODUCT_CASE_WRITE]);
    listProductCases.mockResolvedValue({ items: [], next_cursor: null });
    proposeProductCase.mockResolvedValue(productCase());
    renderList();

    // Re-queried each time: the locked button is a different element (inside
    // its tooltip) from the open one.
    const header = () =>
      screen.getAllByRole("button", {
        name: /Đề xuất sản phẩm/,
      })[0] as HTMLButtonElement;
    await waitFor(() => expect(header().disabled).toBe(false));
    fireEvent.click(header());
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Mã đề xuất"), {
      target: { value: "DX-2026-001" },
    });
    fireEvent.change(within(dialog).getByLabelText("Tên sản phẩm"), {
      target: { value: "Nồi inox" },
    });
    fireEvent.change(within(dialog).getByLabelText("Category"), {
      target: { value: "Nồi" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Đề xuất" }));

    await waitFor(() => expect(proposeProductCase).toHaveBeenCalledTimes(1));
    const [input, key] = proposeProductCase.mock.calls[0]!;
    expect(input).toEqual({
      proposalCode: "DX-2026-001",
      productName: "Nồi inox",
      category: "Nồi",
    });
    expect(typeof key).toBe("string");
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        `/supply-chain/product-cases/${CASE_ID}`,
      ),
    );
  });
});

describe("Hồ sơ phát triển sản phẩm: chi tiết", () => {
  it("answers not found for a case outside the workspace", async () => {
    getProductCase.mockRejectedValue(
      new ApiError(404, { code: "not_found", message: "x", details: {} }),
    );
    renderDetail();
    expect(await screen.findByText("Không tìm thấy hồ sơ")).toBeTruthy();
  });

  it("shows the server's sentence on a failed load, and retries", async () => {
    getProductCase.mockRejectedValueOnce(
      new ApiError(500, {
        code: "internal",
        message: "Máy chủ đang bận",
        details: {},
      }),
    );
    getProductCase.mockResolvedValueOnce(detail());
    renderDetail();

    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Thử lại/ }));
    await waitFor(() => expect(getProductCase).toHaveBeenCalledTimes(2));
    expect(
      await screen.findByRole("heading", { name: "Nồi inox 3 đáy 24cm" }),
    ).toBeTruthy();
  });

  it("shows the state, the round and empty history and rounds", async () => {
    getProductCase.mockResolvedValue(
      detail({ ...productCase(), rounds: [], actions: [] }),
    );
    renderDetail();

    expect(await screen.findByText("Đề xuất")).toBeTruthy();
    expect(screen.getByText("Chưa nhận mẫu")).toBeTruthy();
    expect(
      screen.getByText(/Vòng 1 bắt đầu khi R&D ghi nhận đã nhận mẫu/),
    ).toBeTruthy();
  });

  it("locks a step the viewer's roles lack, with the reason in words", async () => {
    scopes = new Set([ORDERING, EXCEPTIONS]);
    getProductCase.mockResolvedValue(detail());
    renderDetail();

    const pass = await screen.findByRole("button", { name: "Mẫu đạt" });
    expect((pass as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Mẫu đạt: Bước này cần nhiệm vụ R&D/)).toBeTruthy();
    // A step the viewer does hold stays open.
    expect(
      (screen.getByRole("button", { name: "Hủy hồ sơ" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("passes a sample on this round's evaluation only", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(detail());
    renderDetail();
    listProductCaseDocuments.mockResolvedValue([
      evaluation(),
      // Uploaded before round 1 opened: not offered.
      evaluation({
        id: "55555555-5555-4555-8555-555555555555",
        filename: "bien-ban-cu.pdf",
        uploaded_at: "2026-10-05T01:00:00Z",
      }),
    ]);
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "pending_bod_review" }),
      review: "raised",
    });

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu đạt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    expect(await screen.findByTitle(/bien-ban-vong-1\.pdf/)).toBeTruthy();
    expect(screen.queryByTitle(/bien-ban-cu\.pdf/)).toBeNull();
    fireEvent.click(screen.getByTitle(/bien-ban-vong-1\.pdf/));
    fireEvent.click(within(dialog).getByRole("button", { name: "Mẫu đạt" }));

    await waitFor(() => expect(takeProductCaseStep).toHaveBeenCalledTimes(1));
    const [caseId, input] = takeProductCaseStep.mock.calls[0]!;
    expect(caseId).toBe(CASE_ID);
    expect(input).toMatchObject({
      action: "pass_sample",
      documentId: "44444444-4444-4444-8444-444444444444",
    });
    await waitFor(() => expect(getProductCase).toHaveBeenCalledTimes(2));
  });

  it("keeps a refused step's form open with the server's sentence", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(detail());
    renderDetail();
    listProductCaseDocuments.mockResolvedValue([evaluation()]);
    takeProductCaseStep.mockRejectedValue(
      new ApiError(409, {
        code: "conflict",
        message:
          "pass_sample cần sample_evaluation của vòng mẫu hiện tại, thuộc hồ sơ này",
        details: { missing_document_type: "sample_evaluation" },
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu đạt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    fireEvent.click(await screen.findByTitle(/bien-ban-vong-1\.pdf/));
    fireEvent.click(within(dialog).getByRole("button", { name: "Mẫu đạt" }));

    expect(
      await within(dialog).findByText(/cần sample_evaluation/),
    ).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("asks for the missing paper when the round has none", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(detail());
    renderDetail();

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu đạt" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /Chưa có Biên bản đánh giá mẫu nào cho vòng mẫu này/,
      ),
    ).toBeTruthy();
  });

  it("says who may decide a case waiting for BGĐ, links to the approvals, and offers only cancel", async () => {
    scopes = new Set([RND, EXCEPTIONS, ORDERING, "approvals.decide"]);
    getProductCase.mockResolvedValue(
      detail({
        state: "pending_bod_review",
        actions: [
          option("cancel", { required_scope: ORDERING, reason_required: true }),
        ],
        pending_review: {
          approval_id: "77777777-7777-4777-8777-777777777777",
          created_at: "2026-10-06T02:00:00Z",
          required_scope: "supply_chain.approve.bod",
        },
      }),
    );
    renderDetail();

    expect(
      await screen.findByText("Mẫu đã đạt; hồ sơ chờ BGĐ duyệt."),
    ).toBeTruthy();
    expect(
      screen.getByText(
        /Chờ người có quyền BGĐ \(supply_chain\.approve\.bod\) duyệt/,
      ),
    ).toBeTruthy();
    const link = screen.getByRole("link", { name: "Mở trang Duyệt" });
    expect(link.getAttribute("href")).toBe("/approvals");
    expect(screen.queryByText(/chưa có trên hệ thống/)).toBeNull();
    // The page renders one button per action the server offers, so it cannot
    // be where "no BGĐ decision here" is enforced: the server never offers
    // bod_approve/bod_reject (`available_actions`, test_no_state_offers_a_
    // graph_only_action) and refuses them with 422 if sent. What the page
    // owns is rendering what it was given.
    expect(screen.getByRole("button", { name: "Hủy hồ sơ" })).toBeTruthy();
  });

  it("says a case waiting for BGĐ has no review raised yet, and that it will be", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      detail({
        state: "pending_bod_review",
        actions: [
          option("cancel", { required_scope: ORDERING, reason_required: true }),
        ],
        pending_review: null,
      }),
    );
    renderDetail();

    expect(
      await screen.findByText("Mẫu đã đạt; chưa trình được BGĐ."),
    ).toBeTruthy();
    expect(screen.getByText(/Hệ thống tự trình lại/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Mở trang Duyệt" })).toBeNull();
  });

  it("tells the tester when the review was not raised after a pass", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(detail());
    renderDetail();
    listProductCaseDocuments.mockResolvedValue([evaluation()]);
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "pending_bod_review" }),
      review: "not_raised",
    });

    fireEvent.click(await screen.findByRole("button", { name: "Mẫu đạt" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    fireEvent.click(await screen.findByTitle(/bien-ban-vong-1\.pdf/));
    fireEvent.click(within(dialog).getByRole("button", { name: "Mẫu đạt" }));

    expect(
      await screen.findByText(
        "Đã ghi: Mẫu đạt. Chưa trình được BGĐ; hệ thống sẽ tự trình lại.",
      ),
    ).toBeTruthy();
  });

  it("shows BGĐ's decision in the history with its comment and the decider", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      detail({ state: "cancelled", actions: [] }),
    );
    listProductCaseDocuments.mockResolvedValue([]);
    listProductCaseTransitions.mockResolvedValue([
      {
        action: "bod_reject",
        from_state: "pending_bod_review",
        to_state: "cancelled",
        reason: "Giá vốn vượt mục tiêu",
        actor_id: "88888888-8888-4888-8888-888888888888",
        occurred_at: "2026-10-06T03:00:00Z",
      },
    ]);
    // Not `renderDetail()`, which empties the history first.
    render(
      <App>
        <ProductCasePage />
      </App>,
    );

    expect(
      await screen.findByText(/BGĐ không duyệt · Chờ BGĐ duyệt → Đã hủy/),
    ).toBeTruthy();
    expect(screen.getByText("Giá vốn vượt mục tiêu")).toBeTruthy();
    expect(screen.getByText(/người 8888/)).toBeTruthy();
  });
});
