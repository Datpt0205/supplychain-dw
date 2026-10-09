import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { SupplierMessage } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

let workspaceId = "";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId }, hasScope: () => false }),
}));

const listSupplierMessages = vi.fn();
const markSupplierMessageSent = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ listSupplierMessages, markSupplierMessageSent }),
}));

import {
  ALREADY_SENT,
  NOTHING_TO_SEND,
  SupplierMessagesCard,
  mailtoOf,
} from "../supplier-messages-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const MESSAGE_ID = "22222222-2222-4222-8222-222222222222";
const BODY =
  "Kính gửi Chị Lan,\n\nMẫu đã quá hạn 12 ngày.\n\nMong anh/chị phản hồi trước ngày 11/10/2026.\n\nTrân trọng,";

function message(overrides: Partial<SupplierMessage> = {}): SupplierMessage {
  return {
    id: MESSAGE_ID,
    case_kind: "product",
    case_id: CASE_ID,
    purpose: "supplier_reminder",
    status: "drafted",
    supplier_name: "Công ty Gia dụng Minh Phát",
    recipient_name: "Chị Lan",
    recipient_email: "lan@minhphat.vn",
    subject: "[DX-2026-041] Nhắc cập nhật tiến độ: Nồi inox 3 đáy 24cm",
    body: BODY,
    attachments: [],
    citations: [{ text: "Mẫu đã quá hạn 12 ngày.", cites: ["follow_up:x"] }],
    dropped: 1,
    template_version: "1.0.0",
    prompt_id: "supply_chain.draft_supplier_message",
    prompt_version: "1.0.0",
    content_sha256: "a".repeat(64),
    created_at: "2026-10-09T02:00:00Z",
    sent_by: null,
    sent_at: null,
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
  listSupplierMessages.mockReset();
  markSupplierMessageSent.mockReset();
});

function renderCard() {
  workspaceId = crypto.randomUUID();
  render(
    <App>
      <SupplierMessagesCard caseKind="product" caseId={CASE_ID} />
    </App>,
  );
}

describe("SupplierMessagesCard", () => {
  it("shows the drafted text, who it goes to and how many paragraphs were dropped", async () => {
    listSupplierMessages.mockResolvedValue([message()]);
    renderCard();
    expect(await screen.findByText(/Nhắc cập nhật tiến độ/)).toBeTruthy();
    expect(screen.getByText(/lan@minhphat\.vn/)).toBeTruthy();
    expect(screen.getByText(/AI viết thêm 1 đoạn/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sao chép" })).toBeTruthy();
  });

  it("records the text the person copied when they press Đã gửi", async () => {
    listSupplierMessages.mockResolvedValue([message()]);
    markSupplierMessageSent.mockResolvedValue(
      message({ sent_at: "2026-10-09T03:00:00Z" }),
    );
    renderCard();
    fireEvent.click(await screen.findByRole("button", { name: "Đã gửi" }));
    await waitFor(() =>
      expect(markSupplierMessageSent).toHaveBeenCalledWith(
        MESSAGE_ID,
        "a".repeat(64),
        expect.any(String),
      ),
    );
  });

  it("locks Đã gửi on a message already sent, with the reason", async () => {
    listSupplierMessages.mockResolvedValue([
      message({ sent_at: "2026-10-09T03:00:00Z" }),
    ]);
    renderCard();
    const button = await screen.findByRole("button", { name: "Đã gửi" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(ALREADY_SENT).toContain("đã gửi");
  });

  it("offers nothing to send when AI wrote nothing that checks out", async () => {
    listSupplierMessages.mockResolvedValue([
      message({ status: "refused", body: "" }),
    ]);
    renderCard();
    expect(await screen.findByText(NOTHING_TO_SEND)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Đã gửi" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sao chép" })).toBeNull();
  });

  it("opens the person's own mail app with the subject and body", () => {
    const link = mailtoOf(message());
    expect(link.startsWith("mailto:lan%40minhphat.vn?subject=")).toBe(true);
    expect(decodeURIComponent(link)).toContain("Trân trọng,");
    expect(link).not.toContain("+");
  });
});
