import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError } from "@dw/api-client";
import type { Membership } from "../../../lib/auth/auth-context";

const getZaloWorkspace = vi.fn();
const setZaloWorkspace = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getZaloWorkspace, setZaloWorkspace }),
}));

import { ZaloWorkspaceSelect } from "../zalo-workspace-select";

const LABEL = "Workspace dùng cho Zalo";

function membership(
  tenantId: string,
  workspaceId: string,
  tenantName: string,
  workspaceName: string,
): Membership {
  return {
    tenantId,
    tenantSlug: tenantId,
    tenantName,
    workspaceId,
    workspaceSlug: workspaceId,
    workspaceName,
    roles: ["member"],
    scopes: [],
  };
}

const PD = membership("t-1", "w-pd", "Elmich", "Phát triển sản phẩm");
const KHO = membership("t-1", "w-kho", "Elmich", "Kho Bình Dương");

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
});

afterEach(() => {
  cleanup();
  getZaloWorkspace.mockReset();
  setZaloWorkspace.mockReset();
});

function renderSelect(memberships: Membership[]) {
  return render(
    <App>
      <ZaloWorkspaceSelect memberships={memberships} />
    </App>,
  );
}

async function choose(label: string) {
  fireEvent.mouseDown(await screen.findByRole("combobox", { name: LABEL }));
  fireEvent.click(await screen.findByTitle(label));
}

describe("ZaloWorkspaceSelect", () => {
  it("with one workspace there is nothing to choose and nothing is asked", () => {
    const { container } = renderSelect([PD]);
    expect(container.textContent).toBe("");
    expect(getZaloWorkspace).not.toHaveBeenCalled();
  });

  it("offers the viewer's own workspaces and keeps the choice once the server has", async () => {
    getZaloWorkspace.mockResolvedValue({ tenant_id: null, workspace_id: null });
    setZaloWorkspace.mockResolvedValue({
      tenant_id: "t-1",
      workspace_id: "w-kho",
    });
    renderSelect([PD, KHO]);

    expect(await screen.findByText("Chưa chọn")).toBeTruthy();
    expect(
      screen.getByText(/bot sẽ gửi lại đường dẫn tới trang này/),
    ).toBeTruthy();

    await choose("Kho Bình Dương · Elmich");

    await waitFor(() =>
      expect(setZaloWorkspace).toHaveBeenCalledWith("t-1", "w-kho"),
    );
    expect(
      await screen.findByText(
        "Lệnh gửi qua Zalo làm việc trong workspace này.",
      ),
    ).toBeTruthy();
  });

  it("shows the saved choice", async () => {
    getZaloWorkspace.mockResolvedValue({
      tenant_id: "t-1",
      workspace_id: "w-pd",
    });
    renderSelect([PD, KHO]);
    expect(
      await screen.findByTitle("Phát triển sản phẩm · Elmich"),
    ).toBeTruthy();
  });

  it("a refused choice shows the server's sentence and stays unchosen", async () => {
    getZaloWorkspace.mockResolvedValue({ tenant_id: null, workspace_id: null });
    setZaloWorkspace.mockRejectedValue(
      new ApiError(404, {
        code: "not_found",
        message: "Không tìm thấy workspace này trong các workspace của bạn.",
        details: {},
      }),
    );
    renderSelect([PD, KHO]);

    await choose("Kho Bình Dương · Elmich");

    expect(
      await screen.findByText(
        "Không tìm thấy workspace này trong các workspace của bạn.",
      ),
    ).toBeTruthy();
    expect(screen.getByText(/bot sẽ gửi lại đường dẫn/)).toBeTruthy();
  });

  it("hides when the deployment has no Zalo (404 on read)", async () => {
    getZaloWorkspace.mockRejectedValue(
      new ApiError(404, { code: "not_found", message: "x", details: {} }),
    );
    const { container } = renderSelect([PD, KHO]);
    await waitFor(() => expect(container.textContent).toBe(""));
  });
});
