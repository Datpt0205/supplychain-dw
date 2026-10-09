import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ProposalListDetail, ProposalRow } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

const LIST_ID = "11111111-1111-4111-8111-111111111111";
const CASE_ID = "22222222-2222-4222-8222-222222222222";
const USER = "33333333-3333-4333-8333-333333333333";

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "w" }, hasScope: () => true }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: LIST_ID }),
}));

const getProposalList = vi.fn();
const proposeFromList = vi.fn();
const dropFromList = vi.fn();
const listProductCategories = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    getProposalList,
    proposeFromList,
    dropFromList,
    listProductCategories,
  }),
}));

import ProposalListPage from "../proposal-lists/[id]/page";
import { NEEDS_VALUES } from "../../../components/supply-chain/proposal-list-labels";

function row(overrides: Partial<ProposalRow> = {}): ProposalRow {
  return {
    index: 0,
    fields: {
      product_name: {
        value: "Nồi inox 3 đáy 24cm",
        quote: "Nồi inox 3 đáy 24cm",
      },
      proposal_code: { value: "DX-2026-050", quote: "DX-2026-050" },
      supplier_name: null,
      item_code: null,
      image_ref: null,
    },
    gaps: [],
    category: "noi",
    category_reason: "Sản phẩm là nồi",
    priority: "high",
    priority_reason: null,
    findings: [
      { code: "product_seen", message: "Tên sản phẩm trùng một hồ sơ đã có" },
    ],
    decision: null,
    ...overrides,
  };
}

function detail(rows: ProposalRow[]): ProposalListDetail {
  return {
    id: LIST_ID,
    filename: "danh-sach.xlsx",
    content_type: "application/pdf",
    size_bytes: 10,
    uploaded_by: USER,
    created_at: "2026-10-09T02:00:00Z",
    status: "extracted",
    rows,
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
  vi.clearAllMocks();
});

function renderPage() {
  listProductCategories.mockResolvedValue([
    { key: "noi", label: "Nồi" },
    { key: "chao", label: "Chảo" },
  ]);
  render(
    <App>
      <ProposalListPage />
    </App>,
  );
}

describe("ProposalListPage", () => {
  it("shows what the machine read, AI's suggestion beside an empty Category, and the findings", async () => {
    getProposalList.mockResolvedValue(detail([row()]));
    renderPage();
    expect(
      (await screen.findAllByText("danh-sach.xlsx")).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("Tên sản phẩm trùng một hồ sơ đã có")).toBeTruthy();
    expect(screen.getByText("AI gợi ý")).toBeTruthy();
    // The suggestion is never the field's value: proposing waits for a choice.
    const propose = screen.getByRole("button", { name: "Đề xuất" });
    expect(propose.hasAttribute("disabled")).toBe(true);
    expect(NEEDS_VALUES).toContain("nhóm");
  });

  it("proposes the row with the values the PIC accepted", async () => {
    getProposalList.mockResolvedValue(detail([row()]));
    proposeFromList.mockResolvedValue(detail([row()]));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Nồi" }));
    const propose = screen.getByRole("button", { name: "Đề xuất" });
    await waitFor(() => expect(propose.hasAttribute("disabled")).toBe(false));
    fireEvent.click(propose);
    await waitFor(() =>
      expect(proposeFromList).toHaveBeenCalledWith(
        LIST_ID,
        0,
        {
          proposal_code: "DX-2026-050",
          product_name: "Nồi inox 3 đáy 24cm",
          category: "noi",
        },
        expect.any(String),
      ),
    );
  });

  it("shows a decided row as decided, with a link to its case", async () => {
    getProposalList.mockResolvedValue(
      detail([
        row({
          decision: {
            decision: "proposed",
            product_dev_case_id: CASE_ID,
            reason: null,
            decided_by: USER,
            decided_at: "2026-10-09T03:00:00Z",
          },
        }),
      ]),
    );
    renderPage();
    const link = await screen.findByRole("link", {
      name: "Đã đề xuất: mở hồ sơ",
    });
    expect(link.getAttribute("href")).toBe(
      `/supply-chain/product-cases/${CASE_ID}`,
    );
    expect(screen.queryByRole("button", { name: "Đề xuất" })).toBeNull();
  });
});
