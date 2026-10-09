import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PurchaseOrderProposal } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-1" }, hasScope: () => true }),
}));

let online = true;
vi.mock("../../../lib/hooks/use-online", () => ({
  useOnline: () => online,
}));

const getPurchaseOrderProposal = vi.fn();
const approvePurchaseOrder = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getPurchaseOrderProposal, approvePurchaseOrder }),
}));

import { PurchaseOrderCard } from "../purchase-order-card";
import { NO_PO_NUMBER, OFFLINE_APPROVE } from "../purchase-order-labels";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const DRAFT_ID = "33333333-3333-4333-8333-333333333333";
const SHA = "a".repeat(64);

function proposal(
  overrides: Partial<PurchaseOrderProposal> = {},
): PurchaseOrderProposal {
  return {
    draft_id: DRAFT_ID,
    draft_version: 1,
    draft_status: "open",
    content_sha256: SHA,
    findings: [
      {
        code: "term_missing",
        subject: "payment_terms",
        message:
          "PO còn thiếu điều khoản thanh toán; người điền trước khi duyệt",
      },
      {
        code: "term_conflict",
        subject: "unit_price",
        message: "Thư NCC xác nhận khác BM04 ở đơn giá; ô để trống",
      },
    ],
    can_approve: true,
    blocked_reason: null,
    ...overrides,
  };
}

function renderCard(onApproved = vi.fn()) {
  render(
    <App>
      <PurchaseOrderCard caseId={CASE_ID} onApproved={onApproved} />
    </App>,
  );
  return onApproved;
}

const approveButton = () =>
  screen.getByRole("button", { name: /Duyệt PO/ }) as HTMLButtonElement;

afterEach(() => {
  cleanup();
  getPurchaseOrderProposal.mockReset();
  approvePurchaseOrder.mockReset();
  online = true;
});

describe("PurchaseOrderCard", () => {
  it("shows what code found, in words, and asks for the PO number first", async () => {
    getPurchaseOrderProposal.mockResolvedValue(proposal());
    renderCard();
    await waitFor(() => expect(screen.getByText("Mâu thuẫn")).toBeTruthy());
    expect(screen.getByText("Còn thiếu")).toBeTruthy();
    expect(approveButton().disabled).toBe(true);
    expect(screen.getByText(NO_PO_NUMBER)).toBeTruthy();
  });

  it("approves the draft it showed, with the typed number", async () => {
    getPurchaseOrderProposal.mockResolvedValue(proposal());
    approvePurchaseOrder.mockResolvedValue(
      proposal({ draft_status: "confirmed" }),
    );
    const onApproved = renderCard();
    await waitFor(() => expect(screen.getByLabelText("Số PO")).toBeTruthy());
    fireEvent.change(screen.getByLabelText("Số PO"), {
      target: { value: " PO-2026-0101 " },
    });
    await waitFor(() => expect(approveButton().disabled).toBe(false));
    fireEvent.click(approveButton());
    // The confirmation names the number; its own "Duyệt PO" sends it.
    await waitFor(() =>
      expect(
        screen.getAllByText("Tạo PO PO-2026-0101 từ bản nháp này?").length,
      ).toBeGreaterThan(0),
    );
    const buttons = screen.getAllByRole("button", { name: /Duyệt PO/ });
    fireEvent.click(buttons[buttons.length - 1] as HTMLElement);
    await waitFor(() => expect(approvePurchaseOrder).toHaveBeenCalled());
    expect(approvePurchaseOrder.mock.calls[0]?.[1]).toEqual({
      draft_id: DRAFT_ID,
      content_sha256: SHA,
      po_reference: "PO-2026-0101",
    });
    await waitFor(() => expect(onApproved).toHaveBeenCalled());
  });

  it("says why it cannot approve when the server says so", async () => {
    getPurchaseOrderProposal.mockResolvedValue(
      proposal({
        can_approve: false,
        blocked_reason: "Duyệt PO ghi giá: cần quyền ghi dữ liệu thương mại",
      }),
    );
    renderCard();
    await waitFor(() =>
      expect(
        screen.getByText("Duyệt PO ghi giá: cần quyền ghi dữ liệu thương mại"),
      ).toBeTruthy(),
    );
    expect(approveButton().disabled).toBe(true);
  });

  it("offline, nothing is approved and the reason is in words", async () => {
    online = false;
    getPurchaseOrderProposal.mockResolvedValue(proposal());
    renderCard();
    await waitFor(() => expect(screen.getByText(OFFLINE_APPROVE)).toBeTruthy());
    expect(approveButton().disabled).toBe(true);
  });

  it("without a draft says so and offers nothing", async () => {
    getPurchaseOrderProposal.mockResolvedValue(
      proposal({
        draft_id: null,
        draft_version: null,
        draft_status: null,
        content_sha256: null,
        findings: [],
        can_approve: false,
        blocked_reason: "Chưa có PO nháp cho hồ sơ này",
      }),
    );
    renderCard();
    await waitFor(() =>
      expect(screen.getByText(/Chưa có PO nháp/)).toBeTruthy(),
    );
    expect(screen.queryByRole("button", { name: /Duyệt PO/ })).toBeNull();
  });
});
