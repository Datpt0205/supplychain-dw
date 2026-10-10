import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { POCommercial, ProductProfile } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

let workspaceId = "";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId }, hasScope: () => false }),
}));

const getProductProfile = vi.fn();
const saveProductProfile = vi.fn();
const getPOCommercial = vi.fn();
const setPOCommercial = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    getProductProfile,
    saveProductProfile,
    getPOCommercial,
    setPOCommercial,
  }),
}));

import {
  Bm04ProfileCard,
  NO_PRICE_EDIT,
  PRICES_HIDDEN,
} from "../bm04-profile-card";
import { NO_COMMERCIAL_EDIT, POCommercialCard } from "../po-commercial-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";
const SKU_ID = "22222222-2222-4222-8222-222222222222";

function profile(overrides: Partial<ProductProfile> = {}): ProductProfile {
  return {
    product_dev_case_id: CASE_ID,
    version: 2,
    attributes: { product_name: "Nồi inox 24cm", food_contact: true },
    unit_price: { value: null, redacted: true },
    currency: "USD",
    moq: 500,
    lead_time_days: 30,
    incoterm: "FOB",
    schema_version: "1.0.0",
    bm04_schema: {
      schema_version: "1.0",
      policy_id: "supply_chain_bm04_schema",
      policy_version: "1.0.0",
      fields: [
        {
          key: "product_name",
          label: "Tên sản phẩm",
          kind: "text",
          required: true,
          max_length: 200,
        },
        {
          key: "food_contact",
          label: "Tiếp xúc thực phẩm",
          kind: "boolean",
          required: false,
        },
      ],
    },
    prices_visible: false,
    can_edit: true,
    can_edit_prices: false,
    created_by: "33333333-3333-4333-8333-333333333333",
    created_at: "2026-10-09T02:00:00Z",
    ...overrides,
  };
}

function commercial(overrides: Partial<POCommercial> = {}): POCommercial {
  return {
    po_case_id: CASE_ID,
    currency: "USD",
    incoterm: "FOB",
    payment_terms: null,
    payment_terms_redacted: true,
    deposit_percent: { value: null, redacted: true },
    expected_delivery_date: "2026-12-01",
    lines: [
      {
        sku_id: SKU_ID,
        sku_code: "SKU-01",
        variant_label: "Đỏ",
        quantity: 100,
        unit_price: { value: null, redacted: true },
        line_total: { value: null, redacted: true },
      },
    ],
    order_total: { value: null, redacted: true },
    payments: [],
    prices_visible: false,
    can_edit: false,
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
  getProductProfile.mockReset();
  saveProductProfile.mockReset();
  getPOCommercial.mockReset();
  setPOCommercial.mockReset();
});

function renderIn(node: React.ReactNode) {
  workspaceId = crypto.randomUUID();
  render(<App>{node}</App>);
}

describe("Bm04ProfileCard", () => {
  it("draws the tenant's fields and a hidden price as a lock, never a number", async () => {
    getProductProfile.mockResolvedValue(profile());
    renderIn(<Bm04ProfileCard caseId={CASE_ID} />);

    expect(await screen.findByText("Tên sản phẩm")).toBeTruthy();
    expect(screen.getByText("Tiếp xúc thực phẩm")).toBeTruthy();
    expect(screen.getByText(PRICES_HIDDEN)).toBeTruthy();
    expect(screen.getByText("Đã ẩn")).toBeTruthy();
    expect(screen.queryByLabelText("Đơn giá")).toBeNull();
  });

  it("saves without prices when the writer may not set them, so the last price is kept", async () => {
    getProductProfile.mockResolvedValue(profile());
    saveProductProfile.mockResolvedValue(profile({ version: 3 }));
    renderIn(<Bm04ProfileCard caseId={CASE_ID} />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Lưu phiên bản mới" }),
    );
    await waitFor(() => expect(saveProductProfile).toHaveBeenCalledTimes(1));
    const [, body, key] = saveProductProfile.mock.calls[0] ?? [];
    expect(body).not.toHaveProperty("prices");
    expect(body.attributes.product_name).toBe("Nồi inox 24cm");
    expect(typeof key).toBe("string");
  });

  it("locks price and currency for a reader who may see but not set them, with the reason in words", async () => {
    getProductProfile.mockResolvedValue(
      profile({
        prices_visible: true,
        unit_price: { value: "3.7500", redacted: false },
      }),
    );
    renderIn(<Bm04ProfileCard caseId={CASE_ID} />);

    const price = (await screen.findByLabelText("Đơn giá")) as HTMLInputElement;
    expect(price.disabled).toBe(true);
    expect(screen.getByText(NO_PRICE_EDIT)).toBeTruthy();
  });
});

describe("POCommercialCard", () => {
  it("shows every price as a lock and the edit button disabled with its reason", async () => {
    getPOCommercial.mockResolvedValue(commercial());
    renderIn(<POCommercialCard caseId={CASE_ID} />);

    expect(await screen.findByText("SKU-01")).toBeTruthy();
    expect(screen.getByText(PRICES_HIDDEN)).toBeTruthy();
    // Terms, deposit, total, unit price and line total: five locks, no number.
    expect(screen.getAllByText("Đã ẩn")).toHaveLength(5);
    // No amount anywhere: a number before a currency would be a leak.
    expect(screen.queryByText(/\d\s*USD/)).toBeNull();
    const edit = screen.getByRole("button", { name: "Sửa điều khoản" });
    expect((edit as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(NO_COMMERCIAL_EDIT)).toBeTruthy();
  });

  it("shows the server's total and prices to a reader with the scope", async () => {
    getPOCommercial.mockResolvedValue(
      commercial({
        prices_visible: true,
        can_edit: true,
        payment_terms: "30% cọc",
        payment_terms_redacted: false,
        deposit_percent: { value: "30.00", redacted: false },
        lines: [
          {
            sku_id: SKU_ID,
            sku_code: "SKU-01",
            variant_label: "Đỏ",
            quantity: 100,
            unit_price: { value: "2.5000", redacted: false },
            line_total: { value: "250.0000", redacted: false },
          },
        ],
        order_total: { value: "250.0000", redacted: false },
      }),
    );
    renderIn(<POCommercialCard caseId={CASE_ID} />);

    expect(await screen.findByText("30% cọc")).toBeTruthy();
    expect(screen.getAllByText("250 USD")).toHaveLength(2);
    expect(screen.getByText("2,5 USD")).toBeTruthy();
    expect(screen.queryByText("Đã ẩn")).toBeNull();
    const edit = screen.getByRole("button", { name: "Sửa điều khoản" });
    expect((edit as HTMLButtonElement).disabled).toBe(false);
  });
});
