import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PackagingPolicy, SLAPolicy } from "@dw/contracts";

// antd's Table renders slowly under jsdom on Windows.
vi.setConfig({ testTimeout: 30_000 });

const SLA_READ = "supply_chain.sla_policy.read";
const SLA_WRITE = "supply_chain.sla_policy.write";
const DUTIES_READ = "supply_chain.action_duties.read";
const DUTIES_WRITE = "supply_chain.action_duties.write";

// The cache behind each region is keyed by workspace: one per test.
let workspaceId = "";
let scopes = new Set<string>();

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({
    active: { workspaceId },
    hasScope: (scope: string) => scopes.has(scope),
  }),
}));

const api = {
  getSLAPolicy: vi.fn(),
  setSLAPolicy: vi.fn(),
  getPackagingPolicy: vi.fn(),
  setPackagingPolicy: vi.fn(),
};
vi.mock("../../../lib/session", () => ({ apiClient: () => api }));

import SupplyChainSettingsPage from "../settings/page";

const SLA: SLAPolicy = {
  schema_version: "2.0",
  policy_id: "supply_chain_sla",
  policy_version: "2.0.0",
  categories: [
    { key: "noi", label: "Nồi" },
    { key: "chao", label: "Chảo" },
  ],
  default: {
    deposit: { duration: "2d", status: "confirmed", description: "" },
    warehouse_receipt: {
      duration: "5d",
      status: "pending_business_confirmation",
      description: "",
    },
  },
  by_category: {
    chao: {
      sample_testing: { duration: "1d", status: "confirmed", description: "" },
    },
  },
  supplier_update: { reminder_after: "5d", escalation_after: "10d" },
};

const PACKAGING: PackagingPolicy = {
  schema_version: "1.0",
  policy_id: "supply_chain_packaging",
  policy_version: "1.0.0",
  require_pre_production_test: false,
};

afterEach(() => {
  cleanup();
  Object.values(api).forEach((mock) => mock.mockReset());
  scopes = new Set();
});

function renderPage(held: string[]) {
  workspaceId = crypto.randomUUID();
  scopes = new Set(held);
  api.getSLAPolicy.mockResolvedValue(SLA);
  api.getPackagingPolicy.mockResolvedValue(PACKAGING);
  return render(
    <App>
      <SupplyChainSettingsPage />
    </App>,
  );
}

describe("Supply Chain settings", () => {
  it("shows the tenant's numbers, locked with the reason in words for a reader", async () => {
    renderPage([SLA_READ, DUTIES_READ]);

    const deposit = await screen.findByRole("spinbutton", {
      name: "Số ngày mốc đặt cọc",
    });
    expect((deposit as HTMLInputElement).value).toBe("2");
    expect((deposit as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Riêng Category Chảo")).toBeTruthy();
    expect(
      screen.getByText(/sửa SLA cần quyền supply_chain\.sla_policy\.write/),
    ).toBeTruthy();
    const save = screen.getByRole("button", { name: "Lưu SLA" });
    expect((save as HTMLButtonElement).disabled).toBe(true);

    const rule = await screen.findByRole("switch", {
      name: "Bắt buộc test tiền sản xuất đạt trước khi vào sản xuất",
    });
    expect((rule as HTMLButtonElement).disabled).toBe(true);
    expect(
      screen.getByText(/cần quyền supply_chain\.action_duties\.write/),
    ).toBeTruthy();
    expect(api.setSLAPolicy).not.toHaveBeenCalled();
    expect(api.setPackagingPolicy).not.toHaveBeenCalled();
  });

  it("saves the whole SLA document with the changed number", async () => {
    renderPage([SLA_READ, SLA_WRITE]);
    api.setSLAPolicy.mockResolvedValue(SLA);

    const deposit = await screen.findByRole("spinbutton", {
      name: "Số ngày mốc đặt cọc",
    });
    fireEvent.change(deposit, { target: { value: "3" } });
    fireEvent.blur(deposit);
    const save = screen.getByRole("button", { name: "Lưu SLA" });
    await waitFor(() =>
      expect((save as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(save);

    await waitFor(() => expect(api.setSLAPolicy).toHaveBeenCalledTimes(1));
    expect(api.setSLAPolicy).toHaveBeenCalledWith({
      ...SLA,
      default: {
        ...SLA.default,
        deposit: { duration: "3d", status: "confirmed", description: "" },
      },
    });
  });

  it("turns the pre-production rule on for a holder of the duties write", async () => {
    renderPage([DUTIES_READ, DUTIES_WRITE]);
    api.setPackagingPolicy.mockResolvedValue({
      ...PACKAGING,
      require_pre_production_test: true,
    });

    fireEvent.click(
      await screen.findByRole("switch", {
        name: "Bắt buộc test tiền sản xuất đạt trước khi vào sản xuất",
      }),
    );

    await waitFor(() =>
      expect(api.setPackagingPolicy).toHaveBeenCalledWith({
        ...PACKAGING,
        require_pre_production_test: true,
      }),
    );
  });

  it("asks nothing of the server for a card the viewer may not read", async () => {
    renderPage([DUTIES_READ]);

    await screen.findByRole("switch", {
      name: "Bắt buộc test tiền sản xuất đạt trước khi vào sản xuất",
    });
    expect(api.getSLAPolicy).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Lưu SLA" })).toBeNull();
  });
});
