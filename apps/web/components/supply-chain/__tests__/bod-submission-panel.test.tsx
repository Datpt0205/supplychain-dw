import { cleanup, render, screen } from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { DocumentDraft } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "w" }, hasScope: () => false }),
}));

const getDraft = vi.fn();
vi.mock("../../../lib/session", () => ({ apiClient: () => ({ getDraft }) }));

import {
  BodSubmissionPanel,
  NO_SUBMISSION,
  submissionDraftId,
} from "../bod-submission-panel";

const DRAFT_ID = "22222222-2222-4222-8222-222222222222";
const CASE_ID = "11111111-1111-4111-8111-111111111111";

function draft(): DocumentDraft {
  const base = {
    kind: "text" as const,
    required: false,
    rows: null,
    columns: null,
    redacted_columns: [],
    gap: false,
  };
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
    gaps: [],
    sources: [],
    fields: [
      {
        ...base,
        name: "summary",
        label: "Tóm tắt sản phẩm",
        value: "Mẫu đạt ở vòng 1, độ dày đáy 3.2 mm.",
        redacted: false,
        source: {
          document_id: null,
          quote: null,
          edited_by: null,
          ai_written: true,
          cites: ["case"],
        },
      },
      {
        ...base,
        kind: "number",
        name: "unit_price",
        label: "Đơn giá",
        value: null,
        redacted: true,
        source: null,
      },
    ],
    prices_visible: false,
    can_edit: false,
    created_by: CASE_ID,
    created_at: "2026-10-09T02:00:00Z",
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

describe("BodSubmissionPanel", () => {
  it("shows the tờ trình as the viewer may read it: AI-written marked, the price locked", async () => {
    getDraft.mockResolvedValue(draft());
    render(
      <App>
        <BodSubmissionPanel
          payload={{ bod_submission: { draft_id: DRAFT_ID, gaps: [] } }}
        />
      </App>,
    );
    expect(
      await screen.findByText("Mẫu đạt ở vòng 1, độ dày đáy 3.2 mm."),
    ).toBeTruthy();
    expect(screen.getByText("AI viết, đã kiểm dẫn chứng")).toBeTruthy();
    expect(
      screen.getByText(
        "Giá chỉ hiện với người có quyền xem dữ liệu thương mại.",
      ),
    ).toBeTruthy();
    expect(getDraft).toHaveBeenCalledWith(DRAFT_ID);
    expect(document.body.textContent).not.toContain("245");
  });

  it("says there is no tờ trình, and blocks nothing", () => {
    render(
      <App>
        <BodSubmissionPanel payload={{ bod_submission: null }} />
      </App>,
    );
    expect(screen.getByText(NO_SUBMISSION)).toBeTruthy();
    expect(getDraft).not.toHaveBeenCalled();
    expect(submissionDraftId({})).toBeNull();
  });
});
