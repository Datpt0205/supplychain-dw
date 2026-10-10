import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { DocumentDraft, DraftField } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

let workspaceId = "";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId }, hasScope: () => false }),
}));

const listCaseDrafts = vi.fn();
const reviseDraft = vi.fn();
const rejectDraft = vi.fn();
const downloadDraft = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    listCaseDrafts,
    reviseDraft,
    rejectDraft,
    downloadDraft,
  }),
}));

import { DRAFT_CLOSED, DraftsCard, NO_DRAFT_EDIT } from "../drafts-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const DRAFT_ID = "22222222-2222-4222-8222-222222222222";
const DOC_ID = "33333333-3333-4333-8333-333333333333";

function field(overrides: Partial<DraftField>): DraftField {
  return {
    name: "x",
    label: "X",
    kind: "text",
    required: false,
    value: null,
    rows: null,
    columns: null,
    redacted: false,
    redacted_columns: [],
    gap: false,
    source: null,
    ...overrides,
  };
}

function draft(overrides: Partial<DocumentDraft> = {}): DocumentDraft {
  return {
    id: DRAFT_ID,
    lineage_id: DRAFT_ID,
    version: 1,
    case_kind: "product",
    case_id: CASE_ID,
    doc_type: "bod_submission",
    template_id: "supply_chain.bod_submission",
    template_version: "1.0.0",
    title: "TỜ TRÌNH BAN GIÁM ĐỐC",
    status: "open",
    decision_reason: null,
    prompt_id: "supply_chain.draft_bod_submission",
    prompt_version: "1.0.0",
    content_sha256: "a".repeat(64),
    gaps: ["recommendation"],
    sources: [],
    fields: [
      field({
        name: "product_name",
        label: "Tên sản phẩm",
        required: true,
        value: "Nồi inox 24cm",
        source: {
          document_id: DOC_ID,
          quote: "Tên sản phẩm: Nồi inox 24cm",
          edited_by: null,
          ai_written: false,
          cites: [],
        },
      }),
      field({
        name: "unit_price",
        label: "Đơn giá",
        kind: "number",
        redacted: true,
      }),
      field({
        name: "recommendation",
        label: "Đề xuất",
        required: true,
        gap: true,
      }),
    ],
    prices_visible: false,
    can_edit: true,
    created_by: DOC_ID,
    created_at: "2026-10-09T02:00:00Z",
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
  listCaseDrafts.mockReset();
  reviseDraft.mockReset();
  rejectDraft.mockReset();
  downloadDraft.mockReset();
});

function renderCard() {
  workspaceId = crypto.randomUUID();
  render(
    <App>
      <DraftsCard caseKind="product" caseId={CASE_ID} />
    </App>,
  );
}

describe("DraftsCard", () => {
  it("shows a gap as such, a hidden price as a lock, and where a value came from", async () => {
    listCaseDrafts.mockResolvedValue([draft()]);
    renderCard();

    expect(await screen.findByText("TỜ TRÌNH BAN GIÁM ĐỐC")).toBeTruthy();
    expect(screen.getByText("Thiếu")).toBeTruthy();
    expect(screen.getByText("1 ô thiếu")).toBeTruthy();
    expect(screen.getByText("Đã ẩn")).toBeTruthy();
    expect(screen.getByText("AI soạn")).toBeTruthy();
    expect(
      screen.getByText("Máy đọc: “Tên sản phẩm: Nồi inox 24cm”"),
    ).toBeTruthy();
    expect(listCaseDrafts).toHaveBeenCalledWith("product", CASE_ID);
  });

  it("marks words a model wrote as AI-written, never as read or checked", async () => {
    listCaseDrafts.mockResolvedValue([
      draft({
        fields: [
          field({
            name: "recommendation",
            label: "Đề xuất",
            value: "Đề nghị duyệt sản phẩm.",
            source: {
              document_id: null,
              quote: null,
              edited_by: null,
              ai_written: true,
              cites: ["history:1"],
            },
          }),
        ],
      }),
    ]);
    renderCard();
    expect(await screen.findByText("AI viết, đã kiểm dẫn chứng")).toBeTruthy();
    expect(screen.queryByText(/Máy đọc/)).toBeNull();
  });

  it("locks editing with the reason when the caller may not write or the version is closed", async () => {
    listCaseDrafts.mockResolvedValue([
      draft({ can_edit: false }),
      draft({ id: DOC_ID, status: "superseded", version: 2 }),
    ]);
    renderCard();

    await screen.findAllByText("TỜ TRÌNH BAN GIÁM ĐỐC");
    const edits = screen.getAllByRole("button", { name: "Sửa trường" });
    expect(edits.every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect(screen.getByText(NO_DRAFT_EDIT)).toBeTruthy();
    expect(screen.getByText(DRAFT_CLOSED)).toBeTruthy();
  });

  it("sends only the fields a person changed, never a hidden price", async () => {
    listCaseDrafts.mockResolvedValue([draft()]);
    reviseDraft.mockResolvedValue(draft({ version: 2 }));
    renderCard();

    fireEvent.click(await screen.findByRole("button", { name: "Sửa trường" }));
    const recommendation = await screen.findByLabelText("Đề xuất");
    fireEvent.change(recommendation, { target: { value: "Đề nghị duyệt" } });
    fireEvent.click(screen.getByRole("button", { name: "Lưu phiên bản mới" }));

    await waitFor(() => expect(reviseDraft).toHaveBeenCalledTimes(1));
    const [id, values] = reviseDraft.mock.calls[0] ?? [];
    expect(id).toBe(DRAFT_ID);
    expect(values).toEqual({ recommendation: "Đề nghị duyệt" });
  });
});
