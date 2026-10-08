import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The session menu is the second door to /audit beside the nav registry. The
// audit trail is `audit.events` on the API (approval-audit-and-workspace/02),
// so a member holding only the inbox's `approvals.read` must not be offered it
// here either.
let scopes: string[] = [];
vi.mock("../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    status: "ready",
    displayName: "Lan",
    roles: ["member"],
    isPlatformOperator: false,
    logout: vi.fn(),
    hasScope: (scope: string) => scopes.includes(scope),
  }),
}));

import { SessionChip } from "../session-chip";

afterEach(cleanup);

function openMenu(): void {
  render(<SessionChip />);
  fireEvent.click(screen.getByRole("button", { name: /Lan/ }));
}

function auditLinkShown(): boolean {
  return screen.queryByRole("link", { name: /Nhật ký kiểm toán/ }) !== null;
}

describe("session menu", () => {
  it("does not offer the audit log to a member who reads only the inbox", () => {
    scopes = ["approvals.read", "runs.read"];
    openMenu();

    expect(screen.getByRole("menuitem", { name: /Đăng xuất/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Cài đặt cá nhân/ })).toBeTruthy();
    expect(auditLinkShown()).toBe(false);
  });

  it("offers the audit log to a holder of audit.events", () => {
    scopes = ["approvals.read", "runs.read", "audit.events"];
    openMenu();

    expect(auditLinkShown()).toBe(true);
  });
});
