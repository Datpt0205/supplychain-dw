import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The auth mode is a build-time constant; a getter lets each test pick one.
const state = vi.hoisted(() => ({ mode: "oidc" as "oidc" | "dev" }));
vi.mock("../../lib/auth/config", () => ({
  get AUTH_MODE() {
    return state.mode;
  },
}));

vi.mock("../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    status: "unauthenticated",
    error: null,
    login: vi.fn(),
    logout: vi.fn(),
    active: null,
    isPlatformOperator: false,
    hasScope: () => false,
    roles: [],
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/dev-login/layer-check",
  useRouter: () => ({ replace: vi.fn() }),
}));

import { AppFrame } from "../app-frame";

afterEach(cleanup);

describe("AppFrame and the layer-check fixture", () => {
  it("lets the fixture past the gate in a dev-auth build", () => {
    state.mode = "dev";
    render(<AppFrame>fixture content</AppFrame>);
    expect(screen.getByText("fixture content")).toBeTruthy();
  });

  it("keeps the fixture behind the gate in any other build", () => {
    state.mode = "oidc";
    render(<AppFrame>fixture content</AppFrame>);
    expect(screen.queryByText("fixture content")).toBeNull();
    expect(screen.getByRole("button", { name: "Đăng nhập" })).toBeTruthy();
  });
});
