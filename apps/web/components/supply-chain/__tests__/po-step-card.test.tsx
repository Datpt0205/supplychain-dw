import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { POStepProposal } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-1" }, hasScope: () => true }),
}));

let online = true;
vi.mock("../../../lib/hooks/use-online", () => ({
  useOnline: () => online,
}));

const getPOStepProposal = vi.fn();
const approvePOStep = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getPOStepProposal, approvePOStep }),
}));

import { POStepCard } from "../po-step-card";
import {
  FILL_RESULTS,
  HIDDEN_SUGGESTION,
  OFFLINE_STEP,
} from "../po-step-labels";

/** Step 14 (ai-automation/17): QC's verdict, code's suggestion beside it, a
 * reason required only for a fail, a rework request drafted. */
function qc(overrides: Partial<POStepProposal> = {}): POStepProposal {
  return {
    step: "qc",
    action: "pass_qc",
    draft_doc_type: "rework_request",
    draft_id: "22222222-2222-4222-8222-222222222222",
    draft_version: 1,
    draft_status: "open",
    content_sha256: "a".repeat(64),
    findings: [
      {
        code: "qc_stated_differs",
        subject: "qc_report",
        message: "Báo cáo kết luận đạt nhưng số lỗi vượt Ac",
      },
    ],
    results: [
      {
        name: "qc_result",
        kind: "choice",
        label: "Kết quả QC",
        required: true,
        options: ["pass", "fail"],
        required_for: null,
        suggestion: { value: "fail", quote: null, document_id: null },
        redacted: false,
      },
      {
        name: "container_number",
        kind: "text",
        label: "Số container",
        required: false,
        options: [],
        required_for: null,
        suggestion: null,
        redacted: false,
      },
      {
        name: "reason",
        kind: "text",
        label: "Lý do không đạt",
        required: false,
        options: [],
        required_for: "fail",
        suggestion: null,
        redacted: false,
      },
    ],
    proposed: false,
    can_approve: true,
    blocked_reason: null,
    missing_paper: null,
    ...overrides,
  };
}

const CASE_ID = "11111111-1111-4111-8111-111111111111";

function confirmation(overrides: Partial<POStepProposal> = {}): POStepProposal {
  return {
    step: "deposit_payment",
    action: "confirm_deposit",
    draft_doc_type: null,
    draft_id: null,
    draft_version: null,
    draft_status: null,
    content_sha256: null,
    findings: [
      {
        code: "account_differs",
        subject: "bank_transfer_receipt",
        message:
          "Tài khoản thụ hưởng trên UNC KHÁC tài khoản trong danh mục NCC: dấu hiệu lừa đảo đổi tài khoản",
      },
    ],
    results: [
      {
        name: "paid_amount",
        kind: "amount",
        label: "Số tiền đã chi",
        required: true,
        options: [],
        required_for: null,
        suggestion: {
          value: "1.275,00",
          quote: "Số tiền: 1.275,00 USD",
          document_id: null,
        },
        redacted: false,
      },
      {
        name: "paid_on",
        kind: "date",
        label: "Ngày chi",
        required: true,
        options: [],
        required_for: null,
        suggestion: {
          value: "2026-10-11",
          quote: "Ngày: 11/10/2026",
          document_id: null,
        },
        redacted: false,
      },
    ],
    proposed: false,
    can_approve: true,
    blocked_reason: null,
    missing_paper: null,
    ...overrides,
  };
}

function renderCard(onApproved = vi.fn()) {
  render(
    <App>
      <POStepCard caseId={CASE_ID} onApproved={onApproved} />
    </App>,
  );
  return onApproved;
}

const approveButton = () =>
  screen.getByRole("button", { name: /Xác nhận đã cọc/ }) as HTMLButtonElement;

afterEach(() => {
  cleanup();
  getPOStepProposal.mockReset();
  approvePOStep.mockReset();
  online = true;
});

describe("POStepCard", () => {
  it("shows a changed account in red words and keeps the result fields empty", async () => {
    getPOStepProposal.mockResolvedValue(confirmation());
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Tài khoản khác danh mục")).toBeTruthy(),
    );
    expect(screen.getByText("AI chưa đề xuất")).toBeTruthy();
    // AI's reading sits beside the field; the field itself stays empty.
    expect(screen.getByText(/AI đọc được: “1.275,00”/)).toBeTruthy();
    expect(
      (screen.getByLabelText("Số tiền đã chi") as HTMLInputElement).value,
    ).toBe("");
    expect(approveButton().disabled).toBe(true);
    expect(screen.getByText(FILL_RESULTS)).toBeTruthy();
  });

  it("sends the typed results with the step, never the suggestion", async () => {
    getPOStepProposal.mockResolvedValue(confirmation());
    approvePOStep.mockResolvedValue(confirmation({ step: null }));
    const onApproved = renderCard();
    await waitFor(() => expect(screen.getByLabelText("Ngày chi")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Số tiền đã chi"), {
      target: { value: " 1275 " },
    });
    fireEvent.change(screen.getByLabelText("Ngày chi"), {
      target: { value: "2026-10-11" },
    });
    await waitFor(() => expect(approveButton().disabled).toBe(false));
    fireEvent.click(approveButton());
    await waitFor(() =>
      expect(screen.getAllByText("Xác nhận đã cọc?").length).toBeGreaterThan(0),
    );
    const buttons = screen.getAllByRole("button", { name: /Xác nhận đã cọc/ });
    fireEvent.click(buttons[buttons.length - 1] as HTMLElement);
    await waitFor(() => expect(approvePOStep).toHaveBeenCalled());
    expect(approvePOStep.mock.calls[0]?.[1]).toEqual({
      step: "deposit_payment",
      draft_id: null,
      content_sha256: null,
      results: { paid_amount: "1275", paid_on: "2026-10-11" },
    });
    await waitFor(() => expect(onApproved).toHaveBeenCalled());
  });

  it("hides an amount it may not show and says why", async () => {
    getPOStepProposal.mockResolvedValue(
      confirmation({
        results: [
          {
            name: "paid_amount",
            kind: "amount",
            label: "Số tiền đã chi",
            required: true,
            options: [],
            required_for: null,
            suggestion: null,
            redacted: true,
          },
        ],
      }),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText(HIDDEN_SUGGESTION)).toBeTruthy(),
    );
    expect(screen.queryByText(/1.275/)).toBeNull();
  });

  it("says why it cannot approve, and offline nothing is sent", async () => {
    getPOStepProposal.mockResolvedValue(
      confirmation({
        can_approve: false,
        blocked_reason: "Duyệt bước này cần duty của bước",
      }),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Duyệt bước này cần duty của bước")).toBeTruthy(),
    );
    expect(approveButton().disabled).toBe(true);
    cleanup();

    online = false;
    getPOStepProposal.mockResolvedValue(confirmation());
    renderCard();
    await waitFor(() => expect(screen.getByText(OFFLINE_STEP)).toBeTruthy());
    expect(approveButton().disabled).toBe(true);
  });

  it("leaves QC's verdict to QC and asks a reason only for a fail", async () => {
    getPOStepProposal.mockResolvedValue(qc());
    approvePOStep.mockResolvedValue(qc({ step: null }));
    renderCard();
    await waitFor(() =>
      expect(screen.getByText("Kết luận khác số lỗi")).toBeTruthy(),
    );
    // The suggestion is in words beside the empty choice, never chosen.
    expect(screen.getByText("AI gợi ý: Không đạt")).toBeTruthy();
    expect(screen.getByText(/phiếu yêu cầu sửa hàng/)).toBeTruthy();
    const record = () =>
      screen.getByRole("button", {
        name: /Ghi kết quả QC/,
      }) as HTMLButtonElement;
    expect(record().disabled).toBe(true);
    fireEvent.mouseDown(screen.getByRole("combobox", { name: "Kết quả QC" }));
    fireEvent.click(await screen.findByTitle("Không đạt"));
    // A fail without its reason stays locked.
    await waitFor(() => expect(screen.getByText(FILL_RESULTS)).toBeTruthy());
    expect(record().disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Lý do không đạt"), {
      target: { value: "Lỗi nặng vượt Ac" },
    });
    await waitFor(() => expect(record().disabled).toBe(false));
    fireEvent.click(record());
    await waitFor(() =>
      expect(screen.getAllByText("Ghi kết quả QC?").length).toBeGreaterThan(0),
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: /Ghi kết quả QC/ }).at(-1)!,
    );
    await waitFor(() => expect(approvePOStep).toHaveBeenCalledTimes(1));
    expect(approvePOStep.mock.calls[0]?.[1]).toMatchObject({
      step: "qc",
      results: {
        qc_result: "fail",
        container_number: "",
        reason: "Lỗi nặng vượt Ac",
      },
    });
  });

  it("renders nothing for a state no prepared step covers", async () => {
    getPOStepProposal.mockResolvedValue(
      confirmation({ step: null, action: null, findings: [], results: [] }),
    );
    const { container } = render(
      <App>
        <POStepCard caseId={CASE_ID} onApproved={vi.fn()} />
      </App>,
    );
    await waitFor(() => expect(getPOStepProposal).toHaveBeenCalled());
    await waitFor(() =>
      expect(container.querySelector(".ant-card")).toBeNull(),
    );
  });
});
