import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Approval, ApprovalViewOutcome } from "@dw/contracts";
import { formatDateTime } from "../../../lib/dates";

const APPROVAL_ID = "8d6c1f8e-0000-4000-8000-000000000001";
const HOME = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";

let search = new URLSearchParams();
let active = HOME;
const selectWorkspace = vi.fn((workspaceId: string) => {
  active = workspaceId;
});
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: APPROVAL_ID }),
  useSearchParams: () => search,
}));
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId: active },
    memberships: [{ workspaceId: HOME }, { workspaceId: OTHER }],
    selectWorkspace,
  }),
}));

const getApproval = vi.fn();
const viewApproval = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getApproval, viewApproval }),
}));

import ApprovalPage from "../[id]/page";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      // A 320 px phone: every max-width query matches.
      matches: query.includes("max-width"),
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
  getApproval.mockReset();
  viewApproval.mockReset();
  selectWorkspace.mockClear();
  search = new URLSearchParams();
  active = HOME;
});

function approval(overrides: Partial<Approval> = {}): Approval {
  return {
    id: APPROVAL_ID,
    approval_type: "supply_chain.product_action.bod_review",
    reason: "BGĐ duyệt mẫu đã đạt: DX-001 · Chảo 28cm, vòng mẫu 1",
    status: "pending",
    run_id: null,
    payload: {},
    created_at: "2026-10-07T02:00:00Z",
    decided_at: null,
    requires_comment: true,
    required_scope: "supply_chain.approve.bod",
    can_decide: true,
    requested_by_me: false,
    ...overrides,
  };
}

function outcome(
  overrides: Partial<ApprovalViewOutcome> = {},
): ApprovalViewOutcome {
  return {
    viewed_at: "2026-10-07T03:00:00Z",
    requires_comment: true,
    code: null,
    expires_at: null,
    command_approve: null,
    command_reject: null,
    unavailable_reason: null,
    ...overrides,
  };
}

const ISSUED = outcome({
  code: "482193",
  expires_at: "2099-10-07T03:10:00Z",
  command_approve: "DUYỆT 482193",
  command_reject: "KHÔNG 482193 <lý do>",
});

describe("an approval's page", () => {
  it("records the view on open, then issues a code bound to the comment", async () => {
    window.innerWidth = 320;
    getApproval.mockResolvedValue(approval());
    viewApproval.mockResolvedValueOnce(outcome()).mockResolvedValueOnce(ISSUED);
    render(<ApprovalPage />);

    expect(await screen.findByText(/BGĐ duyệt mẫu đã đạt/)).toBeTruthy();
    expect(viewApproval).toHaveBeenCalledWith(APPROVAL_ID, {});
    // A strict type: no code until a comment is written, and it says why.
    const ask = screen.getByRole("button", {
      name: /Lấy mã để quyết qua Zalo/,
    });
    expect(ask.hasAttribute("disabled")).toBe(true);
    expect(
      screen.getByText("Loại yêu cầu này cần nhận xét trước khi lấy mã."),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Nhận xét (bắt buộc)"), {
      target: { value: "Mẫu đạt, đồng ý." },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Lấy mã để quyết qua Zalo/ }),
    );

    expect(await screen.findByText("482193")).toBeTruthy();
    expect(viewApproval).toHaveBeenLastCalledWith(APPROVAL_ID, {
      comment: "Mẫu đạt, đồng ý.",
      issue_code: true,
    });
    expect(document.body.textContent).toContain(
      `hết hạn lúc ${formatDateTime(ISSUED.expires_at)} (giờ Việt Nam)`,
    );
    const approve = screen.getByText("DUYỆT 482193");
    const reject = screen.getByText("KHÔNG 482193 <lý do>");
    // At 320 px the commands wrap rather than push the page sideways.
    expect(approve.closest(".break-all")).not.toBeNull();
    expect(reject.closest(".break-all")).not.toBeNull();
    expect(
      screen.getAllByRole("button", { name: /Sao chép|Copy/i }).length,
    ).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("button", { name: /Lấy mã mới/ })).toBeTruthy();
  });

  it("says a lapsed code has expired instead of showing its digits", async () => {
    getApproval.mockResolvedValue(approval({ requires_comment: false }));
    viewApproval
      .mockResolvedValueOnce(outcome({ requires_comment: false }))
      .mockResolvedValueOnce({ ...ISSUED, expires_at: "2000-01-01T00:00:00Z" });
    render(<ApprovalPage />);
    fireEvent.click(
      await screen.findByRole("button", { name: /Lấy mã để quyết qua Zalo/ }),
    );

    expect(await screen.findByText(/Mã đã hết hạn/)).toBeTruthy();
    expect(screen.queryByText("DUYỆT 482193")).toBeNull();
  });

  it.each([
    ["web_only", "Loại yêu cầu này chỉ quyết trên web."],
    [
      "requester",
      "Bạn đã tạo yêu cầu này nên không tự quyết được (tách nhiệm).",
    ],
    ["not_pending", "Yêu cầu này đã được quyết hoặc đã hủy."],
    ["cannot_decide", "Bạn không có quyền quyết yêu cầu này."],
    ["channel_off", "Quyết qua Zalo chưa bật ở hệ thống này."],
  ] as const)(
    "offers no code when the server says %s, and says why",
    async (reason, text) => {
      getApproval.mockResolvedValue(approval());
      viewApproval.mockResolvedValue(outcome({ unavailable_reason: reason }));
      render(<ApprovalPage />);

      expect(await screen.findByText(text)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /Lấy mã/ })).toBeNull();
    },
  );

  it("sends a viewer with no Zalo link to the settings page", async () => {
    getApproval.mockResolvedValue(approval());
    viewApproval.mockResolvedValue(
      outcome({ unavailable_reason: "not_linked" }),
    );
    render(<ApprovalPage />);

    const link = await screen.findByRole("link", {
      name: "Mở trang Cài đặt cá nhân",
    });
    expect(link.getAttribute("href")).toBe("/settings");
  });

  it("switches to the approval's workspace from the link before loading", async () => {
    search = new URLSearchParams({ workspace: OTHER });
    getApproval.mockResolvedValue(approval());
    viewApproval.mockResolvedValue(outcome());
    const { rerender } = render(<ApprovalPage />);

    expect(selectWorkspace).toHaveBeenCalledWith(OTHER);
    expect(getApproval).not.toHaveBeenCalled();
    await act(async () => rerender(<ApprovalPage />));
    await waitFor(() => expect(getApproval).toHaveBeenCalledWith(APPROVAL_ID));
  });

  it("does not load an approval of a workspace the viewer is not in", async () => {
    search = new URLSearchParams({
      workspace: "33333333-3333-4333-8333-333333333333",
    });
    render(<ApprovalPage />);

    expect(
      await screen.findByText(
        /không thuộc workspace nào của bạn|Không tìm thấy/,
      ),
    ).toBeTruthy();
    expect(getApproval).not.toHaveBeenCalled();
    expect(viewApproval).not.toHaveBeenCalled();
  });
});
