import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { PreProductionChecklist } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "w" }, hasScope: () => true }),
}));

const getPreProductionChecklist = vi.fn();
const recordPreProductionMeasurement = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    getPreProductionChecklist,
    recordPreProductionMeasurement,
  }),
}));

import {
  PRE_PRODUCTION_CLOSED,
  PRE_PRODUCTION_NO_DUTY,
  PreProductionCard,
} from "../pre-production-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";

function checklist(
  overrides: Partial<PreProductionChecklist> = {},
): PreProductionChecklist {
  return {
    case_id: CASE_ID,
    attempt: 2,
    open: true,
    can_record: true,
    suggestion: null,
    rows: [
      {
        key: "base_thickness",
        label: "Độ dày đáy",
        kind: "number",
        unit: "mm",
        min: "3",
        max: null,
        standard: "≥ 3 mm",
        value: "2.5",
        note: null,
        verdict: "fail",
      },
      {
        key: "appearance",
        label: "Ngoại quan",
        kind: "check",
        unit: null,
        min: null,
        max: null,
        standard: "Đạt",
        value: null,
        note: null,
        verdict: "unmeasured",
      },
    ],
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
  vi.clearAllMocks();
});

function renderCard() {
  render(
    <App>
      <PreProductionCard caseId={CASE_ID} />
    </App>,
  );
}

describe("PreProductionCard", () => {
  it("shows the attempt, code's verdicts and no suggestion while one is unmeasured", async () => {
    getPreProductionChecklist.mockResolvedValue(checklist());
    renderCard();
    expect(await screen.findByText("Số đo test trước SX (lần 2)")).toBeTruthy();
    expect(screen.getByText("Không đạt")).toBeTruthy();
    expect(screen.getByText("Chưa đo")).toBeTruthy();
    expect(screen.getByText("chưa đủ số đo để gợi ý")).toBeTruthy();
  });

  it("shows code's suggestion in words beside the table", async () => {
    getPreProductionChecklist.mockResolvedValue(
      checklist({ suggestion: "pass_pre_production_test" }),
    );
    renderCard();
    expect(
      await screen.findByText("Gợi ý của hệ thống theo số đo:"),
    ).toBeTruthy();
    expect(screen.getAllByText("Đạt").length).toBeGreaterThan(0);
  });

  it("records what R&D typed for one criterion", async () => {
    getPreProductionChecklist.mockResolvedValue(checklist());
    recordPreProductionMeasurement.mockResolvedValue(checklist());
    renderCard();
    fireEvent.change(await screen.findByLabelText("Số đo Độ dày đáy"), {
      target: { value: "3,2" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu" })[0]!);
    await waitFor(() =>
      expect(recordPreProductionMeasurement).toHaveBeenCalledWith(
        CASE_ID,
        { criterion: "base_thickness", value: "3,2", note: null },
        expect.any(String),
      ),
    );
  });

  it.each([
    [{ can_record: false }, PRE_PRODUCTION_NO_DUTY],
    [{ open: false, can_record: false }, PRE_PRODUCTION_CLOSED],
  ])("locks entry with the reason in words (%o)", async (over, reason) => {
    getPreProductionChecklist.mockResolvedValue(checklist(over));
    renderCard();
    expect(await screen.findByText(reason)).toBeTruthy();
    for (const button of screen.getAllByRole("button", { name: "Lưu" }))
      expect(button.hasAttribute("disabled")).toBe(true);
  });
});
