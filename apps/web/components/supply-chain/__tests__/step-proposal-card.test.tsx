import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { StepProposal } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-1" }, hasScope: () => false }),
}));

const getStepProposal = vi.fn();
const decideStepProposal = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getStepProposal, decideStepProposal }),
}));

import { CANNOT_DECIDE, STALE, StepProposalCard } from "../step-proposal-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const APPROVAL_ID = "22222222-2222-4222-8222-222222222222";
const DRAFT_ID = "33333333-3333-4333-8333-333333333333";

function proposal(overrides: Partial<StepProposal> = {}): StepProposal {
  return {
    prepared: true,
    action: "pass_sample",
    status: "proposed",
    reason: null,
    recorded_at: "2026-10-09T02:00:00Z",
    approval_id: APPROVAL_ID,
    required_scope: "supply_chain.duty.rnd",
    stale: false,
    can_decide: true,
    physical: true,
    drafts: [
      {
        draft_id: DRAFT_ID,
        doc_type: "sample_evaluation",
        version: 1,
        gaps: ["criteria"],
      },
    ],
    sources: [{ doc_type: "sample_evaluation", document_id: DRAFT_ID }],
    action_document_id: null,
    findings: [
      {
        code: "draft_gaps",
        subject: "sample_evaluation",
        message: "Bản nháp còn thiếu: criteria",
      },
    ],
    result_fields: [
      {
        name: "conclusion",
        label: "Kết luận",
        kind: "text",
        suggestion: {
          value: "Đạt",
          quote: "Kết quả: Đạt",
          document_id: DRAFT_ID,
        },
        choices: [],
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
  getStepProposal.mockReset();
  decideStepProposal.mockReset();
});

const onDecided = vi.fn();

function renderCard() {
  render(
    <App>
      <StepProposalCard caseId={CASE_ID} onDecided={onDecided} />
    </App>,
  );
}

describe("StepProposalCard", () => {
  it("shows the result field empty with AI's reading beside it, never in it", async () => {
    getStepProposal.mockResolvedValue(proposal());
    renderCard();

    const field = (await screen.findByLabelText(
      "Kết luận",
    )) as HTMLInputElement;
    expect(field.value).toBe("");
    expect(screen.getByText("AI đọc được: “Đạt”")).toBeTruthy();
    expect(screen.getByText("Người duyệt: R&D")).toBeTruthy();
    expect(screen.getByText(/Bản nháp còn thiếu: criteria/)).toBeTruthy();
    // A physical step is decided here, not by a Zalo code.
    expect(screen.queryByText(/lấy mã duyệt qua Zalo/)).toBeNull();
  });

  it("refuses to approve with an empty result and sends what the person typed", async () => {
    getStepProposal.mockResolvedValue(proposal());
    decideStepProposal.mockResolvedValue(
      proposal({ status: "applied", approval_id: null }),
    );
    renderCard();

    fireEvent.change(await screen.findByLabelText("Nhận xét"), {
      target: { value: "Đã test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Duyệt chuyển bước" }));
    await screen.findByText("Nhập kết quả");
    expect(decideStepProposal).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Kết luận"), {
      target: { value: "Đạt yêu cầu" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Duyệt chuyển bước" }));
    await waitFor(() => expect(decideStepProposal).toHaveBeenCalledTimes(1));
    const [caseId, body] = decideStepProposal.mock.calls[0] ?? [];
    expect(caseId).toBe(CASE_ID);
    expect(body).toEqual({
      approval_id: APPROVAL_ID,
      approve: true,
      comment: "Đã test",
      result: { conclusion: "Đạt yêu cầu" },
    });
  });

  it("offers the step's outcomes to choose from, none chosen, AI's suggestion beside", async () => {
    getStepProposal.mockResolvedValue(
      proposal({
        result_fields: [
          {
            name: "conclusion",
            label: "Kết luận",
            kind: "text",
            suggestion: {
              value: "Cần chỉnh sửa",
              quote: "Tiêu chí không đạt: Độ dày đáy",
              document_id: null,
            },
            choices: [
              { value: "pass", label: "Đạt" },
              { value: "revise", label: "Cần chỉnh sửa" },
              { value: "reject", label: "Hủy" },
            ],
          },
        ],
      }),
    );
    renderCard();
    expect(await screen.findByText("Chọn kết luận")).toBeTruthy();
    expect(screen.getByText("AI đọc được: “Cần chỉnh sửa”")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Nhận xét"), {
      target: { value: "Theo số đo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Duyệt chuyển bước" }));
    await screen.findByText("Nhập kết quả");
    expect(decideStepProposal).not.toHaveBeenCalled();
  });

  it("locks the decision with the reason when the viewer lacks the duty", async () => {
    getStepProposal.mockResolvedValue(proposal({ can_decide: false }));
    renderCard();

    const approve = await screen.findByRole("button", {
      name: "Duyệt chuyển bước",
    });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(CANNOT_DECIDE)).toBeTruthy();
  });

  it("says a proposal is stale and decides nothing on it", async () => {
    getStepProposal.mockResolvedValue(proposal({ stale: true }));
    renderCard();

    const approve = await screen.findByRole("button", {
      name: "Duyệt chuyển bước",
    });
    expect((approve as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText(STALE).length).toBeGreaterThan(0);
  });

  it("names why a step could not be prepared", async () => {
    getStepProposal.mockResolvedValue(
      proposal({
        status: "not_prepared",
        approval_id: null,
        reason: "source_not_read_yet:supplier_quotation",
      }),
    );
    renderCard();
    expect(
      await screen.findByText(
        "Chưa chuẩn bị được: máy đang đọc chứng từ nguồn (Báo giá, thông số NCC)",
      ),
    ).toBeTruthy();
  });

  it("offers the approval page (a Zalo code) for a step with nothing to type", async () => {
    getStepProposal.mockResolvedValue(
      proposal({
        physical: false,
        result_fields: [],
        action: "confirm_with_supplier",
      }),
    );
    renderCard();
    const link = await screen.findByText(
      "Mở trang duyệt (lấy mã duyệt qua Zalo)",
    );
    expect(link.closest("a")?.getAttribute("href")).toBe(
      `/approvals/${APPROVAL_ID}?workspace=ws-1`,
    );
  });

  it("draws nothing for a tenant that prepares no step here", async () => {
    getStepProposal.mockResolvedValue(proposal({ prepared: false }));
    const { container } = render(
      <App>
        <StepProposalCard caseId={CASE_ID} onDecided={onDecided} />
      </App>,
    );
    await waitFor(() => expect(getStepProposal).toHaveBeenCalled());
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});
