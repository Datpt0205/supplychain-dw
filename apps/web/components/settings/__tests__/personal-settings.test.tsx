import { cleanup, render, screen } from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// Two people in one tenant. The page is rendered as An; Bình's workspace must
// never appear, and the page must not call anything that could list him.
const AN = {
  displayName: "Nguyễn Văn An",
  email: "an.nguyen@example.com",
  memberships: [
    {
      tenantId: "t-1",
      tenantSlug: "elmich",
      tenantName: "Elmich",
      workspaceId: "w-pd",
      workspaceSlug: "phat-trien",
      workspaceName: "Phát triển sản phẩm",
      roles: ["member"],
      scopes: [],
    },
  ],
};
const BINH_WORKSPACE = "Kho Bình Dương";

const getMe = vi.fn();
const getZaloStatus = vi.fn();
// Records every client method the page touches, known or not, so a page that
// started listing members or a directory would show up here.
const touched = new Set<string>();
const methods: Record<string, unknown> = { getMe, getZaloStatus };
const client = new Proxy(methods, {
  get(target, prop: string) {
    touched.add(prop);
    return target[prop] ?? vi.fn().mockResolvedValue(undefined);
  },
});
vi.mock("../../../lib/session", () => ({ apiClient: () => client }));
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ ...AN, active: AN.memberships[0] }),
}));

import { PersonalSettings } from "../personal-settings";

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
  touched.clear();
  getMe.mockReset();
  getZaloStatus.mockReset();
});

describe("PersonalSettings", () => {
  it("shows the viewer's own profile and memberships, and nobody else's", async () => {
    getMe.mockResolvedValue({
      tenant_id: "t-1",
      workspace_id: "w-pd",
      principal_id: "u-an",
      roles: ["member"],
      scopes: [],
      clearance: "internal",
      plan_id: "professional",
      feature_flags: [],
    });
    getZaloStatus.mockResolvedValue({ linked: false });

    render(
      <App>
        <PersonalSettings />
      </App>,
    );

    expect(
      await screen.findByRole("heading", { name: "Cài đặt cá nhân" }),
    ).toBeTruthy();
    expect(screen.getByText("Nguyễn Văn An")).toBeTruthy();
    expect(screen.getByText("an.nguyen@example.com")).toBeTruthy();
    expect(screen.getByText("Phát triển sản phẩm")).toBeTruthy();
    expect(screen.getByText("Đang mở")).toBeTruthy();
    expect(screen.queryByText(BINH_WORKSPACE)).toBeNull();
    expect(await screen.findByText("Chưa kết nối")).toBeTruthy();

    // Only the caller's own reads: no directory, no member list.
    expect([...touched].sort()).toEqual(["getMe", "getZaloStatus"]);
    expect(getMe).toHaveBeenCalledTimes(1);
  });

  it("a failed /me shows the server's sentence beside a retry, not a blank", async () => {
    const { ApiError } = await import("@dw/api-client");
    getMe.mockRejectedValue(
      new ApiError(503, {
        code: "upstream_unavailable",
        message: "Máy chủ tạm thời không phản hồi.",
        details: {},
      }),
    );
    getZaloStatus.mockResolvedValue({ linked: true });

    render(
      <App>
        <PersonalSettings />
      </App>,
    );

    expect(
      await screen.findByText("Máy chủ tạm thời không phản hồi."),
    ).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Thử lại/ }).length).toBe(1);
  });
});
