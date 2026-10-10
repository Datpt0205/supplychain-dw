import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";

const listSupportStaff = vi.fn();
const listSupportRequests = vi.fn();
const assignSupportRequest = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    listSupportStaff,
    listSupportRequests,
    assignSupportRequest,
    addSupportStaff: vi.fn(),
    removeSupportStaff: vi.fn(),
  }),
}));

import { SupportConsole } from "../support-console";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const REQUEST = {
  grant_id: "g-1",
  code: "SG-0001",
  tenant_id: "t-1",
  tenant_name: "Công ty A",
  workspace_id: "w-1",
  workspace_name: "Main",
  resource_type: "workspace",
  resource_label: "Main",
  scope_set_key: "ctx.read",
  scope_set_label: "Đọc hồ sơ",
  duration_hours: 72,
  reason: "đọc sai trang 3",
  requested_at: "2026-10-08T02:00:00Z",
};

// antd's Select in jsdom takes seconds under a full parallel run.
describe("the operators' support console", { timeout: 20_000 }, () => {
  it("assigns a waiting request only to someone on the support team", async () => {
    listSupportStaff.mockResolvedValue([
      {
        user_id: "s-1",
        email: "lan@ops.vn",
        display_name: "Lan",
        note: null,
        added_at: "2026-10-01T00:00:00Z",
      },
    ]);
    listSupportRequests.mockResolvedValue([REQUEST]);
    assignSupportRequest.mockResolvedValue({
      grant_id: "g-1",
      code: "SG-0001",
      tenant_id: "t-1",
      staff_user_id: "s-1",
      activated_at: "2026-10-08T03:00:00Z",
      expires_at: "2026-10-11T03:00:00Z",
    });
    render(
      <App>
        <SupportConsole emailOptions={[]} />
      </App>,
    );

    expect(await screen.findByText("SG-0001")).toBeTruthy();
    const assign = screen.getByRole("button", { name: /Giao$/ });
    expect(assign.hasAttribute("disabled")).toBe(true);

    fireEvent.mouseDown(
      screen.getByRole("combobox", { name: "Nhân viên hỗ trợ cho SG-0001" }),
    );
    // Only the support team is offered.
    await screen.findByTitle("Lan");
    expect(document.querySelectorAll(".ant-select-item-option")).toHaveLength(
      1,
    );
    fireEvent.click(await screen.findByTitle("Lan"));

    fireEvent.click(screen.getByRole("button", { name: /Giao$/ }));
    await waitFor(() =>
      expect(assignSupportRequest).toHaveBeenCalledWith("g-1", "s-1"),
    );
    // The reason the customer wrote is shown to the operator, nothing else.
    expect(within(document.body).queryByText(/ctx\.read/)).toBeNull();
  });
});
