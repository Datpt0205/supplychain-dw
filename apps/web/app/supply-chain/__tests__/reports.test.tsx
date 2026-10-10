import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

vi.setConfig({ testTimeout: 30_000 });

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "w" }, hasScope: () => true }),
}));

const getWeeklyReport = vi.fn();
const summarizeWeeklyReport = vi.fn();
const getSupplierScorecard = vi.fn();
const getAiAcceptance = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({
    getWeeklyReport,
    summarizeWeeklyReport,
    getSupplierScorecard,
    getAiAcceptance,
  }),
}));

import ReportsPage from "../reports/page";
import { ESTIMATE_NOTE } from "../../../components/supply-chain/report-labels";

const WEEK = {
  week_start: "2026-10-05",
  week_end: "2026-10-11",
  figures: [
    {
      key: "proposed",
      label: "Hồ sơ SP mới",
      value: 2,
      cases: ["DX-1", "DX-2"],
    },
    {
      key: "qc_reworks",
      label: "QC trả hàng làm lại",
      value: 1,
      cases: ["PO-7"],
    },
  ],
};

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

function renderPage() {
  getWeeklyReport.mockResolvedValue(WEEK);
  getSupplierScorecard.mockResolvedValue([
    {
      supplier: "Minh Phát",
      po_total: 3,
      po_open: 2,
      po_completed: 1,
      qc_reworks: 1,
      rounds_passed: 1,
      rounds_revised: 1,
      rounds_rejected: 0,
      lines_differing: 2,
      sample_pass_rate: 0.5,
    },
  ]);
  getAiAcceptance.mockResolvedValue({
    since: "2026-07-12T00:00:00Z",
    policy_version: "1.0.0",
    rows: [
      {
        doc_type: "sample_evaluation",
        drafted: 4,
        as_is: 1,
        edited: 1,
        rejected: 1,
        open: 1,
        minutes_saved: 45,
      },
    ],
  });
  render(
    <App>
      <ReportsPage />
    </App>,
  );
}

describe("ReportsPage", () => {
  it("shows the week's figures with the cases they count", async () => {
    renderPage();
    expect(await screen.findByText("DX-1, DX-2")).toBeTruthy();
    expect(screen.getByText("QC trả hàng làm lại")).toBeTruthy();
  });

  it("shows the scorecard and the acceptance counts, the minutes as an estimate", async () => {
    renderPage();
    expect(await screen.findByText("Minh Phát")).toBeTruthy();
    expect(screen.getByText("50%")).toBeTruthy();
    expect(await screen.findByText("45")).toBeTruthy();
    expect(screen.getByText(ESTIMATE_NOTE)).toBeTruthy();
  });

  it("shows the AI summary as AI-written with the figures each sentence rests on", async () => {
    summarizeWeeklyReport.mockResolvedValue({
      report: WEEK,
      sentences: [
        {
          text: "Tuần này có 2 hồ sơ SP mới.",
          cites: [{ key: "figure:proposed", label: "Hồ sơ SP mới" }],
        },
      ],
      dropped: 1,
    });
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "AI tóm tắt tuần" }),
    );
    expect(await screen.findByText("Tuần này có 2 hồ sơ SP mới.")).toBeTruthy();
    expect(screen.getByText("AI viết, đã kiểm với số")).toBeTruthy();
    expect(screen.getByText("Dựa trên: Hồ sơ SP mới")).toBeTruthy();
  });
});
