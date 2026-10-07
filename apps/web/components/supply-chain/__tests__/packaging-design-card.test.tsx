import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  type PackagingDesign,
  type PackagingStepOption,
} from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

let workspaceId = "";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId }, hasScope: () => false }),
}));

const getPackagingDesign = vi.fn();
const takePackagingStep = vi.fn();
const listCaseDocuments = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    getPackagingDesign,
    takePackagingStep,
    listCaseDocuments,
  }),
}));

import {
  PACKAGING_ACTION_LABEL,
  PackagingDesignCard,
} from "../packaging-design-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const REPORT_ID = "22222222-2222-4222-8222-222222222222";

function steps(allowed: (action: string) => boolean): PackagingStepOption[] {
  return (
    Object.keys(PACKAGING_ACTION_LABEL) as PackagingStepOption["action"][]
  ).map((action) => ({
    action,
    duty: action.endsWith("_test") ? "rnd" : "ordering",
    allowed: allowed(action),
    requires_reason:
      action.startsWith("request_") || action === "fail_pre_production_test",
    requires_document: action.endsWith("_test"),
  }));
}

function design(overrides: Partial<PackagingDesign> = {}): PackagingDesign {
  return {
    po_case_id: CASE_ID,
    colour_status: "approved",
    design_status: "approved",
    pre_production_sample_received_at: "2026-10-07T02:00:00Z",
    pre_production_test: "pending",
    version: 3,
    case_state: "pre_production",
    require_pre_production_test: true,
    steps: steps((action) => action.endsWith("_test")),
    history: [
      {
        action: "approve_colour",
        reason: null,
        note: "Đã báo TP MKT: màu đạt, chuyển sang làm bao bì",
        document_id: null,
        actor_id: "33333333-3333-4333-8333-333333333333",
        occurred_at: "2026-10-06T02:00:00Z",
      },
    ],
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
  getPackagingDesign.mockReset();
  takePackagingStep.mockReset();
  listCaseDocuments.mockReset();
});

function renderCard(onStep = vi.fn()) {
  workspaceId = crypto.randomUUID();
  render(
    <App>
      <PackagingDesignCard caseId={CASE_ID} onStep={onStep} />
    </App>,
  );
  return onStep;
}

describe("PackagingDesignCard", () => {
  it("shows the four sub-steps, the step-13 rule and the MKT note", async () => {
    getPackagingDesign.mockResolvedValue(design());
    renderCard();

    expect(await screen.findByText("Mẫu màu")).toBeTruthy();
    expect(
      screen.getByText(
        "Hồ sơ chỉ vào Sản xuất khi R&D đạt test trước sản xuất.",
      ),
    ).toBeTruthy();
    expect(screen.getByText("Chưa test")).toBeTruthy();
    expect(
      screen.getByText("Đã báo TP MKT: màu đạt, chuyển sang làm bao bì"),
    ).toBeTruthy();
  });

  it("locks a step the caller's duty does not cover, with the reason in words", async () => {
    getPackagingDesign.mockResolvedValue(design());
    renderCard();

    const approve = await screen.findByRole("button", {
      name: PACKAGING_ACTION_LABEL.approve_colour,
    });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Bước này cần nhiệm vụ Cung ứng; vai của bạn chưa có."),
    ).toBeTruthy();
    const pass = screen.getByRole("button", {
      name: PACKAGING_ACTION_LABEL.pass_pre_production_test,
    });
    expect((pass as HTMLButtonElement).disabled).toBe(false);
  });

  it("locks every step once the case has left step 12", async () => {
    getPackagingDesign.mockResolvedValue(
      design({ case_state: "production", steps: steps(() => true) }),
    );
    renderCard();

    const pass = await screen.findByRole("button", {
      name: PACKAGING_ACTION_LABEL.pass_pre_production_test,
    });
    expect((pass as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText("Chỉ làm khi Hồ sơ PO ở Chuẩn bị sản xuất."),
    ).toBeTruthy();
  });

  it("passes the test on the chosen report and shows a refusal in the server's words", async () => {
    getPackagingDesign.mockResolvedValue(design());
    listCaseDocuments.mockResolvedValue([
      {
        id: REPORT_ID,
        case_kind: "po",
        case_id: CASE_ID,
        doc_type: "pre_production_test_report",
        filename: "bien-ban-test.pdf",
        content_type: "application/pdf",
        size_bytes: 10,
        sha256: "a".repeat(64),
        version: 1,
        uploaded_by: "33333333-3333-4333-8333-333333333333",
        uploaded_at: "2026-10-07T03:00:00Z",
      },
      {
        id: "44444444-4444-4444-8444-444444444444",
        case_kind: "po",
        case_id: CASE_ID,
        doc_type: "purchase_order",
        filename: "po.pdf",
        content_type: "application/pdf",
        size_bytes: 10,
        sha256: "a".repeat(64),
        version: 1,
        uploaded_by: "33333333-3333-4333-8333-333333333333",
        uploaded_at: "2026-10-07T03:00:00Z",
      },
    ]);
    takePackagingStep.mockRejectedValueOnce(
      new ApiError(409, {
        code: "conflict",
        message:
          "pass_pre_production_test cần pre_production_test_report của hồ sơ này",
        details: {},
      }),
    );
    const onStep = renderCard();

    fireEvent.click(
      await screen.findByRole("button", {
        name: PACKAGING_ACTION_LABEL.pass_pre_production_test,
      }),
    );
    fireEvent.mouseDown(
      await screen.findByRole("combobox", { name: "Biên bản test trước SX" }),
    );
    expect(screen.queryByTitle(/po\.pdf/)).toBeNull();
    fireEvent.click(await screen.findByTitle(/bien-ban-test\.pdf/));
    fireEvent.click(
      screen.getAllByRole("button", {
        name: PACKAGING_ACTION_LABEL.pass_pre_production_test,
      })[1]!,
    );

    expect(
      await screen.findByText(
        "pass_pre_production_test cần pre_production_test_report của hồ sơ này",
      ),
    ).toBeTruthy();
    expect(takePackagingStep).toHaveBeenCalledWith(
      CASE_ID,
      {
        action: "pass_pre_production_test",
        reason: undefined,
        documentId: REPORT_ID,
      },
      expect.any(String),
    );
    expect(onStep).not.toHaveBeenCalled();

    takePackagingStep.mockResolvedValueOnce(design());
    fireEvent.click(
      screen.getAllByRole("button", {
        name: PACKAGING_ACTION_LABEL.pass_pre_production_test,
      })[1]!,
    );
    await waitFor(() => expect(onStep).toHaveBeenCalled());
    // The same payload retried keeps its key: one step, not two.
    const keys = takePackagingStep.mock.calls.map((call) => call[2]);
    expect(keys[0]).toBe(keys[1]);
  });
});
