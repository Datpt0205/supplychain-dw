import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { SampleChecklist } from "@dw/api-client";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "w" }, hasScope: () => true }),
}));

const getSampleChecklist = vi.fn();
const recordSampleMeasurement = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getSampleChecklist, recordSampleMeasurement }),
}));

import { NO_RECORD, SampleChecklistCard } from "../sample-checklist-card";

const CASE_ID = "11111111-1111-4111-8111-111111111111";

function checklist(overrides: Partial<SampleChecklist> = {}): SampleChecklist {
  return {
    case_id: CASE_ID,
    sample_round: 1,
    can_record: true,
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
      <SampleChecklistCard caseId={CASE_ID} />
    </App>,
  );
}

describe("SampleChecklistCard", () => {
  it("shows code's verdict for each criterion, unmeasured as such", async () => {
    getSampleChecklist.mockResolvedValue(checklist());
    renderCard();
    expect(await screen.findByText("Không đạt")).toBeTruthy();
    expect(screen.getByText("Chưa đo")).toBeTruthy();
    expect(screen.getByText("2.5 mm")).toBeTruthy();
  });

  it("records what R&D typed for one criterion", async () => {
    getSampleChecklist.mockResolvedValue(checklist());
    recordSampleMeasurement.mockResolvedValue(checklist());
    renderCard();
    fireEvent.change(await screen.findByLabelText("Số đo Độ dày đáy"), {
      target: { value: "3,2" },
    });
    fireEvent.click(screen.getAllByRole("button", { name: "Lưu" })[0]!);
    await waitFor(() =>
      expect(recordSampleMeasurement).toHaveBeenCalledWith(
        CASE_ID,
        { criterion: "base_thickness", value: "3,2", note: null },
        expect.any(String),
      ),
    );
  });

  it("locks entry with the reason for a viewer who may not record", async () => {
    getSampleChecklist.mockResolvedValue(checklist({ can_record: false }));
    renderCard();
    expect(await screen.findByText(NO_RECORD)).toBeTruthy();
    for (const button of screen.getAllByRole("button", { name: "Lưu" }))
      expect(button.hasAttribute("disabled")).toBe(true);
  });
});
