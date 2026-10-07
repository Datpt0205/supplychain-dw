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
const placeProductOrder = vi.fn();
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
    placeProductOrder,
  }),
}));

import ProductCasesPage from "../product-cases/page";
import ProductCasePage from "../product-cases/[id]/page";

const ORDERING = "supply_chain.duty.ordering";
const RND = "supply_chain.duty.rnd";
const EXCEPTIONS = "supply_chain.duty.exceptions";
const SUPPLY_LEAD = "supply_chain.duty.supply_lead";
const DOCUMENT_WRITE = "supply_chain.document.write";
// When round 1 opened, the bound the server gives a round step's paper.
const ROUND_OPENED = "2026-10-05T03:00:00Z";
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
    signoff_round: 0,
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
    documents_since: null,
    unmet: [],
    ...overrides,
  };
}

const TESTING_ACTIONS: ProductActionOption[] = [
  option("pass_sample", {
    required_scope: RND,
    document_type: "sample_evaluation",
    document_required: true,
    documents_since: ROUND_OPENED,
  }),
  option("request_revision", {
    required_scope: RND,
    reason_required: true,
    document_type: "sample_revision_request",
    document_required: true,
    documents_since: ROUND_OPENED,
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
        opened_at: ROUND_OPENED,
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
    item_code: null,
    skus: [],
    po_case_id: null,
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
    placeProductOrder,
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
        /Chưa có Biên bản đánh giá mẫu nào cho bước này/,
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
          step: null,
          step_label: null,
          step_no: null,
          steps: [],
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
    // The review's own page: it decides on the web or issues a Zalo code.
    const link = screen.getByRole("link", { name: /Mở yêu cầu duyệt/ });
    expect(link.getAttribute("href")).toBe(
      "/approvals/77777777-7777-4777-8777-777777777777",
    );
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
    expect(screen.queryByRole("link", { name: /Mở yêu cầu duyệt/ })).toBeNull();
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

  // --- steps 7-8 (ticket 03) ---------------------------------------------------

  // When BGĐ approved: the bound the server gives step 7's paper.
  const APPROVED = "2026-10-06T05:00:00Z";
  const BM04_ID = "66666666-6666-4666-8666-666666666666";

  function atStep(
    state: "profile_in_progress" | "supplier_confirmation",
    since = APPROVED,
  ): ProductCaseDetail {
    const step =
      state === "profile_in_progress"
        ? option("complete_profile", {
            required_scope: RND,
            document_type: "product_profile_bm04",
            document_required: true,
            documents_since: since,
          })
        : option("confirm_with_supplier", {
            required_scope: SUPPLY_LEAD,
            document_type: "supplier_confirmation_email",
            document_required: true,
            documents_since: since,
          });
    return detail({
      state,
      actions: [
        step,
        option("cancel", { required_scope: ORDERING, reason_required: true }),
      ],
    });
  }

  it("uploads the BM04 inside the step, chooses it and completes the profile", async () => {
    scopes = new Set([RND, DOCUMENT_WRITE]);
    getProductCase.mockResolvedValue(atStep("profile_in_progress"));
    renderDetail();
    uploadProductCaseDocument.mockResolvedValue(
      evaluation({
        id: BM04_ID,
        doc_type: "product_profile_bm04",
        filename: "bm04.xlsx",
        uploaded_at: "2026-10-06T06:00:00Z",
      }),
    );
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "supplier_confirmation" }),
      review: null,
    });

    fireEvent.click(
      await screen.findByRole("button", { name: "Hoàn tất BM04" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /Chưa có Profile SP \(BM04\) nào cho bước này/,
      ),
    ).toBeTruthy();
    const input = dialog.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["x"], "bm04.xlsx")] },
    });
    fireEvent.click(
      await within(dialog).findByRole("button", {
        name: /Tải lên Profile SP \(BM04\)/,
      }),
    );

    await waitFor(() =>
      expect(uploadProductCaseDocument).toHaveBeenCalledTimes(1),
    );
    const [uploadCase, uploaded] = uploadProductCaseDocument.mock.calls[0]!;
    expect(uploadCase).toBe(CASE_ID);
    expect(uploaded).toMatchObject({ docType: "product_profile_bm04" });
    fireEvent.click(
      await within(dialog).findByRole("button", { name: "Hoàn tất BM04" }),
    );
    await waitFor(() => expect(takeProductCaseStep).toHaveBeenCalledTimes(1));
    expect(takeProductCaseStep.mock.calls[0]![1]).toMatchObject({
      action: "complete_profile",
      documentId: BM04_ID,
    });
  });

  it("offers the supplier's email only from when the case reached step 8", async () => {
    scopes = new Set([SUPPLY_LEAD]);
    getProductCase.mockResolvedValue(atStep("supplier_confirmation"));
    renderDetail();
    listProductCaseDocuments.mockResolvedValue([
      evaluation({
        doc_type: "supplier_confirmation_email",
        filename: "ncc-xac-nhan.eml",
        uploaded_at: "2026-10-06T07:00:00Z",
      }),
      evaluation({
        id: "77777777-7777-4777-8777-777777777777",
        doc_type: "supplier_confirmation_email",
        filename: "ncc-cu.eml",
        uploaded_at: "2026-10-06T04:00:00Z",
      }),
    ]);

    fireEvent.click(
      await screen.findByRole("button", { name: "Đã thống nhất với NCC" }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    expect(await screen.findByTitle(/ncc-xac-nhan\.eml/)).toBeTruthy();
    expect(screen.queryByTitle(/ncc-cu\.eml/)).toBeNull();
  });

  it("locks step 8 for R&D with the duty it needs, in words", async () => {
    scopes = new Set([RND, DOCUMENT_WRITE]);
    getProductCase.mockResolvedValue(atStep("supplier_confirmation"));
    renderDetail();

    const confirm = await screen.findByRole("button", {
      name: "Đã thống nhất với NCC",
    });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(
        /Đã thống nhất với NCC: Bước này cần nhiệm vụ TP Cung ứng/,
      ),
    ).toBeTruthy();
  });

  it("locks uploading inside the step for a viewer without the document write", async () => {
    scopes = new Set([SUPPLY_LEAD]);
    getProductCase.mockResolvedValue(atStep("supplier_confirmation"));
    renderDetail();

    fireEvent.click(
      await screen.findByRole("button", { name: "Đã thống nhất với NCC" }),
    );
    const dialog = await screen.findByRole("dialog");
    const upload = await within(dialog).findByRole("button", {
      name: /Tải lên Email xác nhận của NCC/,
    });
    expect((upload as HTMLButtonElement).disabled).toBe(true);
    expect(
      within(dialog).getByText(/Chỉ người có quyền tải chứng từ lên/),
    ).toBeTruthy();
  });

  it("offers no paper when the server gives no bound", async () => {
    scopes = new Set([RND, DOCUMENT_WRITE]);
    getProductCase.mockResolvedValue(
      detail({
        state: "profile_in_progress",
        actions: [
          option("complete_profile", {
            required_scope: RND,
            document_type: "product_profile_bm04",
            document_required: true,
            documents_since: null,
          }),
        ],
      }),
    );
    renderDetail();
    listProductCaseDocuments.mockResolvedValue([
      evaluation({ doc_type: "product_profile_bm04", filename: "bm04.xlsx" }),
    ]);

    fireEvent.click(
      await screen.findByRole("button", { name: "Hoàn tất BM04" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText(
        /Chưa có Profile SP \(BM04\) nào cho bước này/,
      ),
    ).toBeTruthy();
  });
});

describe("Hồ sơ phát triển sản phẩm: mã hàng, SKU, trình ký (bước 9)", () => {
  const CODING_ACTIONS: ProductActionOption[] = [
    option("issue_item_code", { required_scope: ORDERING }),
    option("add_sku", { required_scope: ORDERING, unmet: ["item_code"] }),
    option("remove_sku", { required_scope: ORDERING }),
    option("submit_for_signoff", {
      required_scope: ORDERING,
      unmet: ["item_code", "sku"],
    }),
    option("cancel", { required_scope: ORDERING, reason_required: true }),
  ];
  const ITEM = { id: "55555555-5555-4555-8555-555555555555", code: "MH-0001" };
  const SKU = {
    id: "66666666-6666-4666-8666-666666666666",
    sku_code: "MH-0001-RED",
    variant_label: "Đỏ 24cm",
    planned_quantity: 300,
  };

  function coding(overrides: Partial<ProductCaseDetail> = {}) {
    return detail({
      state: "item_coding",
      actions: CODING_ACTIONS,
      ...overrides,
    });
  }

  it("issues the item code and locks SKUs and submitting until it exists, with the reason", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(coding());
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "item_coding" }),
      review: null,
    });
    renderDetail();

    expect(await screen.findByText("Chưa có mã hàng.")).toBeTruthy();
    // The server's `unmet`, in words, beside the locked controls.
    expect(
      screen.getByText("Thêm SKU: Cần mã hàng chính thức trước."),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Trình ký: Cần mã hàng chính thức và ít nhất một SKU trước.",
      ),
    ).toBeTruthy();
    const submit = screen.getByRole("button", { name: "Trình ký" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    // The coding steps are the card's, not buttons in the step list.
    expect(screen.queryByRole("button", { name: "Bỏ SKU" })).toBeNull();

    fireEvent.change(screen.getByLabelText("Mã hàng"), {
      target: { value: "MH-0001" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cấp mã hàng" }));

    await waitFor(() => expect(takeProductCaseStep).toHaveBeenCalled());
    const [caseId, input, key] = takeProductCaseStep.mock.calls[0]!;
    expect(caseId).toBe(CASE_ID);
    expect(input).toEqual({ action: "issue_item_code", itemCode: "MH-0001" });
    expect(typeof key).toBe("string");
  });

  it("shows a code taken in the company at its field", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(coding());
    takeProductCaseStep.mockRejectedValue(
      new ApiError(409, {
        code: "conflict",
        message: "mã hàng MH-0001 đã có trong công ty",
        details: {
          constraint: "uq_item_codes_tenant_id_code",
          item_code: "MH-0001",
        },
      }),
    );
    renderDetail();

    fireEvent.change(await screen.findByLabelText("Mã hàng"), {
      target: { value: "MH-0001" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cấp mã hàng" }));

    expect(
      await screen.findByText("mã hàng MH-0001 đã có trong công ty"),
    ).toBeTruthy();
    // At the field, not as a page-level alert.
    expect(screen.queryByRole("alert", { name: /đã có/ })).toBeNull();
  });

  it("adds a SKU under the item code and removes one after a confirmation", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      coding({
        item_code: ITEM,
        skus: [SKU],
        actions: CODING_ACTIONS.map((o) =>
          o.action === "add_sku" || o.action === "submit_for_signoff"
            ? { ...o, unmet: [] }
            : o,
        ),
      }),
    );
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "item_coding" }),
      review: null,
    });
    renderDetail();

    expect(await screen.findByText("MH-0001-RED")).toBeTruthy();
    expect(screen.getByText("Đỏ 24cm")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Mã SKU"), {
      target: { value: "MH-0001-BLUE" },
    });
    fireEvent.change(screen.getByLabelText("Biến thể"), {
      target: { value: "Xanh 24cm" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Thêm SKU" }));
    await waitFor(() =>
      expect(takeProductCaseStep.mock.calls[0]![1]).toEqual({
        action: "add_sku",
        sku: { skuCode: "MH-0001-BLUE", variantLabel: "Xanh 24cm" },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Bỏ" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getAllByText("Bỏ SKU MH-0001-RED?").length,
    ).toBeGreaterThan(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Bỏ SKU" }));
    await waitFor(() =>
      expect(takeProductCaseStep.mock.calls[1]![1]).toEqual({
        action: "remove_sku",
        skuId: SKU.id,
      }),
    );
    // A coded case may be submitted.
    expect(
      (screen.getByRole("button", { name: "Trình ký" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("locks coding for a role without the ordering duty, with the duty named", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(coding({ item_code: ITEM, skus: [SKU] }));
    renderDetail();

    expect(
      await screen.findByText(/Cấp mã hàng|Sửa mã hàng/, {
        selector: "span",
      }),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Mã hàng mới")).toBeNull();
    expect(
      screen.getAllByText(/Bước này cần nhiệm vụ Cung ứng/).length,
    ).toBeGreaterThan(0);
  });

  it("while waiting for sign-off shows the order, the step waiting and its link, and locks the codes", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      detail({
        state: "pending_signoff",
        signoff_round: 1,
        item_code: ITEM,
        skus: [SKU],
        actions: [
          option("cancel", { required_scope: ORDERING, reason_required: true }),
        ],
        pending_review: {
          approval_id: "88888888-8888-4888-8888-888888888888",
          created_at: "2026-10-07T02:00:00Z",
          required_scope: "supply_chain.approve.accounting",
          step: "accounting",
          step_label: "Kế toán",
          step_no: 2,
          steps: [
            { step: "bod", label: "BGĐ" },
            { step: "accounting", label: "Kế toán" },
          ],
        },
      }),
    );
    renderDetail();

    expect(
      await screen.findByText("Hồ sơ chờ ký: bước 2/2, Kế toán."),
    ).toBeTruthy();
    expect(screen.getByText("Đã ký")).toBeTruthy();
    expect(screen.getByText("Đang chờ ký")).toBeTruthy();
    expect(
      screen.getByText(
        /Chờ người có quyền Kế toán \(supply_chain\.approve\.accounting\)/,
      ),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: /Mở yêu cầu ký/ }).getAttribute("href"),
    ).toBe("/approvals/88888888-8888-4888-8888-888888888888");
    expect(
      screen.getByText(
        "Thêm SKU: Hồ sơ đang chờ ký; mã hàng và SKU khóa tới khi có kết quả ký.",
      ),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Mã SKU")).toBeNull();
    expect(screen.getByRole("button", { name: "Hủy hồ sơ" })).toBeTruthy();
  });

  it("says the sign-off request is not visible or not raised yet, honestly", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      detail({
        state: "pending_signoff",
        item_code: ITEM,
        skus: [SKU],
        actions: [
          option("cancel", { required_scope: ORDERING, reason_required: true }),
        ],
        pending_review: null,
      }),
    );
    renderDetail();

    expect(
      await screen.findByText("Đã trình ký; chưa thấy yêu cầu ký."),
    ).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Mở yêu cầu ký/ })).toBeNull();
  });

  it("tells who submitted when the sign-off was not raised", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      coding({
        item_code: ITEM,
        skus: [SKU],
        actions: CODING_ACTIONS.map((o) => ({ ...o, unmet: [] })),
      }),
    );
    takeProductCaseStep.mockResolvedValue({
      ...productCase({ state: "pending_signoff" }),
      review: "not_raised",
    });
    renderDetail();

    fireEvent.click(await screen.findByRole("button", { name: "Trình ký" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Trình ký" }));

    expect(
      await screen.findByText(
        "Đã ghi: Trình ký. Chưa tạo được yêu cầu ký; hệ thống sẽ tự trình lại.",
      ),
    ).toBeTruthy();
  });
});

describe("Hồ sơ phát triển sản phẩm: ĐẶT HÀNG", () => {
  const PO_CASE_ID = "77777777-7777-4777-8777-777777777777";
  const ITEM = { id: "55555555-5555-4555-8555-555555555555", code: "MH-0001" };
  const SKU = {
    id: "66666666-6666-4666-8666-666666666666",
    sku_code: "MH-0001-RED",
    variant_label: "Đỏ 24cm",
    planned_quantity: 300,
  };

  function ready(overrides: Partial<ProductCaseDetail> = {}) {
    return detail({
      state: "ready_to_order",
      signoff_round: 1,
      item_code: ITEM,
      skus: [SKU],
      actions: [
        option("place_order", { required_scope: ORDERING }),
        option("cancel", { required_scope: ORDERING, reason_required: true }),
      ],
      ...overrides,
    });
  }

  it("offers ĐẶT HÀNG once, beside the other steps, never as a generic step", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(ready());
    renderDetail();

    const order = await screen.findByRole("button", { name: "ĐẶT HÀNG" });
    expect((order as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getAllByRole("button", { name: "ĐẶT HÀNG" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Hủy hồ sơ" })).toBeTruthy();
  });

  it("locks ĐẶT HÀNG for a role without the duty, with the reason in words", async () => {
    scopes = new Set([RND]);
    getProductCase.mockResolvedValue(ready());
    renderDetail();

    const order = await screen.findByRole("button", { name: "ĐẶT HÀNG" });
    expect((order as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/^ĐẶT HÀNG: Bước này cần nhiệm vụ Cung ứng/),
    ).toBeTruthy();
  });

  it("locks ĐẶT HÀNG while the case still lacks something, with what", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      ready({
        actions: [
          option("place_order", { required_scope: ORDERING, unmet: ["sku"] }),
        ],
      }),
    );
    renderDetail();

    const order = await screen.findByRole("button", { name: "ĐẶT HÀNG" });
    expect((order as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("ĐẶT HÀNG: Cần ít nhất một SKU trước."),
    ).toBeTruthy();
  });

  it("confirms what opens, then orders at its own route and links the PO case", async () => {
    scopes = new Set([ORDERING]);
    getProductCase
      .mockResolvedValueOnce(ready())
      .mockResolvedValue(
        ready({ state: "ordered", actions: [], po_case_id: PO_CASE_ID }),
      );
    placeProductOrder.mockResolvedValue({
      ...productCase({ state: "ordered" }),
      po_case_id: PO_CASE_ID,
    });
    renderDetail();

    fireEvent.click(await screen.findByRole("button", { name: "ĐẶT HÀNG" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Chờ tạo PO, chưa có số PO/)).toBeTruthy();
    expect(within(dialog).getByText(/Category Nồi/)).toBeTruthy();
    // Nothing is sent before the confirmation.
    expect(placeProductOrder).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "ĐẶT HÀNG" }));

    await waitFor(() => expect(placeProductOrder).toHaveBeenCalled());
    const [caseId, key] = placeProductOrder.mock.calls[0]!;
    expect(caseId).toBe(CASE_ID);
    expect(typeof key).toBe("string");
    expect(takeProductCaseStep).not.toHaveBeenCalled();
    expect(
      (await screen.findByRole("link", { name: "Hồ sơ PO" })).getAttribute(
        "href",
      ),
    ).toBe(`/supply-chain/po-cases/${PO_CASE_ID}`);
  });

  it("shows the server's sentence when the case was already ordered, and reloads", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(ready());
    placeProductOrder.mockRejectedValue(
      new ApiError(409, {
        code: "conflict",
        message: "Hồ sơ đã đặt hàng.",
        details: {},
      }),
    );
    renderDetail();

    fireEvent.click(await screen.findByRole("button", { name: "ĐẶT HÀNG" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "ĐẶT HÀNG" }));

    expect(await screen.findByText("Hồ sơ đã đặt hàng.")).toBeTruthy();
    await waitFor(() => expect(getProductCase).toHaveBeenCalledTimes(2));
  });

  it("links an ordered case to the PO case it opened, and offers no step", async () => {
    scopes = new Set([ORDERING]);
    getProductCase.mockResolvedValue(
      ready({ state: "ordered", actions: [], po_case_id: PO_CASE_ID }),
    );
    renderDetail();

    expect(
      (await screen.findByRole("link", { name: "Hồ sơ PO" })).getAttribute(
        "href",
      ),
    ).toBe(`/supply-chain/po-cases/${PO_CASE_ID}`);
    expect(screen.getAllByText("Đã đặt hàng").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "ĐẶT HÀNG" })).toBeNull();
    expect(
      screen.getByText("Thêm SKU: Đã đặt hàng; mã hàng và SKU đã chốt."),
    ).toBeTruthy();
  });
});
