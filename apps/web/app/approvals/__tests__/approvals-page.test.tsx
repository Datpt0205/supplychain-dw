import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Approval } from "@dw/contracts";

const BOD_SCOPE = "supply_chain.approve.bod";
const REASON = `Chỉ người có quyền ${BOD_SCOPE} được quyết yêu cầu này`;

// The cache behind the list is keyed by workspace, so each test gets its own.
let session: { workspaceId: string; scopes: string[] } = {
  workspaceId: "",
  scopes: [],
};
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId: session.workspaceId },
    hasScope: (scope: string) => session.scopes.includes(scope),
  }),
}));

const listApprovals = vi.fn();
const decideApproval = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ listApprovals, decideApproval }),
}));

import ApprovalsPage from "../page";

beforeAll(() => {
  // antd's responsive helpers ask for media queries jsdom does not implement.
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
  // An open antd Tooltip measures itself; jsdom has no ResizeObserver.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(() => {
  cleanup();
  listApprovals.mockReset();
  decideApproval.mockReset();
});

function approval(overrides: Partial<Approval>): Approval {
  return {
    id: "8d6c1f8e-0000-4000-8000-000000000001",
    approval_type: "supply_chain.product_action.approve",
    reason: "BGĐ duyệt phát triển sản phẩm",
    status: "pending",
    run_id: null,
    payload: {},
    created_at: "2026-10-05T08:00:00Z",
    decided_at: null,
    requires_comment: false,
    required_scope: BOD_SCOPE,
    requested_by_me: false,
    ...overrides,
  };
}

async function renderWith(scopes: string[], item: Approval) {
  session = { workspaceId: crypto.randomUUID(), scopes };
  listApprovals.mockResolvedValue({ items: [item], next_cursor: null });
  render(<ApprovalsPage />);
  return {
    approve: (await screen.findByRole("button", {
      name: "Duyệt",
    })) as HTMLButtonElement,
    reject: screen.getByRole("button", {
      name: "Từ chối",
    }) as HTMLButtonElement,
  };
}

describe("/approvals and the stamped scope (ADR 0020)", () => {
  it("locks both decisions, with the reason in words, for a viewer without the scope", async () => {
    const { approve, reject } = await renderWith(
      ["approvals.read", "approvals.decide"],
      approval({}),
    );

    expect(approve.disabled).toBe(true);
    expect(reject.disabled).toBe(true);
    // Beside the buttons, so touch and keyboard users read it too.
    expect(screen.getByText(REASON)).toBeTruthy();

    // And in a tooltip on the (disabled) button's wrapper.
    fireEvent.mouseEnter(approve.parentElement as HTMLElement);
    const tooltip = await screen.findByRole("tooltip");
    expect(within(tooltip).getByText(REASON)).toBeTruthy();

    fireEvent.click(approve);
    expect(decideApproval).not.toHaveBeenCalled();
  });

  it("enables both for a viewer holding the stamped scope", async () => {
    const { approve, reject } = await renderWith(
      ["approvals.read", "approvals.decide", BOD_SCOPE],
      approval({}),
    );

    expect(approve.disabled).toBe(false);
    expect(reject.disabled).toBe(false);
    expect(screen.queryByText(REASON)).toBeNull();
  });

  it("keeps withdraw open to the requester who lacks the scope", async () => {
    const { approve, reject } = await renderWith(
      ["approvals.read", "approvals.decide"],
      approval({ requested_by_me: true }),
    );

    expect(approve.disabled).toBe(true);
    expect(reject.disabled).toBe(false);
    expect(screen.getByText(/Bạn vẫn rút được yêu cầu của mình/)).toBeTruthy();
  });

  it("changes nothing for an approval with no stamp", async () => {
    const { approve, reject } = await renderWith(
      ["approvals.read", "approvals.decide"],
      approval({ required_scope: null }),
    );

    expect(approve.disabled).toBe(false);
    expect(reject.disabled).toBe(false);
    expect(screen.queryByText(/Chỉ người có quyền/)).toBeNull();
  });

  it("reads the stamp, not the approval type", async () => {
    // Same type as the locked case above; only the stamp differs.
    const { approve } = await renderWith(
      ["approvals.read", "approvals.decide", "supply_chain.approve.accounting"],
      approval({ required_scope: "supply_chain.approve.accounting" }),
    );

    expect(approve.disabled).toBe(false);
  });
});

describe("/approvals, each state of the list", () => {
  it("says nothing is waiting when nothing is", async () => {
    session = { workspaceId: crypto.randomUUID(), scopes: ["approvals.read"] };
    listApprovals.mockResolvedValue({ items: [], next_cursor: null });
    render(<ApprovalsPage />);
    expect(
      await screen.findByText(/Không có yêu cầu nào chờ quyết/),
    ).toBeTruthy();
  });

  it("shows the server's sentence when the list fails, not its code", async () => {
    session = { workspaceId: crypto.randomUUID(), scopes: ["approvals.read"] };
    const { ApiError } = await import("@dw/api-client");
    listApprovals.mockRejectedValue(
      new ApiError(500, {
        code: "internal",
        message: "Máy chủ đang bận",
        details: {},
      }),
    );
    render(<ApprovalsPage />);
    expect(await screen.findByText("Máy chủ đang bận")).toBeTruthy();
    expect(screen.queryByText(/internal/)).toBeNull();
  });

  it("shows a pending request's status, reason and time in Vietnam", async () => {
    session = { workspaceId: crypto.randomUUID(), scopes: ["approvals.read"] };
    listApprovals.mockResolvedValue({
      items: [approval({ required_scope: null })],
      next_cursor: null,
    });
    render(<ApprovalsPage />);
    expect(await screen.findByText("Chờ quyết")).toBeTruthy();
    expect(screen.getByText("BGĐ duyệt phát triển sản phẩm")).toBeTruthy();
    expect(
      screen.getByText(/15:00 05\/10\/2026 \(giờ Việt Nam\)/),
    ).toBeTruthy();
    // Without approvals.decide the request is read-only, and says so.
    expect(screen.getByText(/chỉ để xem/)).toBeTruthy();
  });
});
