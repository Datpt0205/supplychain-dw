import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Approval } from "@dw/contracts";
import type { ApprovalHost } from "../../../lib/approvals/types";

// A context with its own inbox for `ctx.*` approvals, plugged in the way a
// real one is: through the registry, nowhere else.
const HOSTS: ApprovalHost[] = [
  {
    prefix: "ctx.",
    inbox: { label: "Mở hộp duyệt", href: (a) => `/ctx/inbox/${a.id}` },
  },
];
vi.mock("../../../lib/approvals/registry", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("../../../lib/approvals/registry")>();
  return {
    ...actual,
    approvalClient: (type: string) => actual.clientFor(HOSTS, type),
    approvalInbox: (approval: Approval) => actual.inboxFor(HOSTS, approval),
  };
});

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId: crypto.randomUUID() },
    hasScope: (scope: string) =>
      ["approvals.read", "approvals.decide"].includes(scope),
  }),
}));

const listApprovals = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ listApprovals, decideApproval: vi.fn() }),
}));

import ApprovalsPage from "../page";

afterEach(() => {
  cleanup();
  listApprovals.mockReset();
});

function approval(id: string, approval_type: string): Approval {
  return {
    id,
    approval_type,
    reason: `lý do ${id}`,
    status: "pending",
    run_id: null,
    payload: {},
    created_at: "2026-10-08T00:00:00Z",
    decided_at: null,
    requires_comment: false,
    required_scope: null,
    can_decide: true,
    requested_by_me: false,
  };
}

describe("/approvals and a context's own inbox", () => {
  it("links a context's approval to its inbox instead of deciding it here", async () => {
    listApprovals.mockResolvedValue({
      items: [approval("a-ctx", "ctx.x"), approval("a-tool", "tool.y")],
      next_cursor: null,
    });
    render(<ApprovalsPage />);

    const link = await screen.findByRole("link", { name: /Mở hộp duyệt/ });
    expect(link.getAttribute("href")).toBe("/ctx/inbox/a-ctx");

    // The ctx card carries no comment box and no decision; the tool card
    // keeps both, for the same viewer holding approvals.decide.
    const ctxCard = link.closest(".ant-card") as HTMLElement;
    expect(ctxCard.querySelector("textarea")).toBeNull();
    expect(ctxCard.textContent).not.toMatch(/Duyệt|Từ chối/);
    expect(screen.getAllByRole("button", { name: /^Duyệt$/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /^Từ chối$/ })).toHaveLength(
      1,
    );
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
  });
});
