import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PortfolioSummary } from "@dw/contracts";

vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId: "ws-A" } }),
}));

const SUMMARY: PortfolioSummary = {
  active_case_count: 3,
  sla_breached_count: 1,
  update_overdue_count: 2,
  by_state: [
    {
      state: "waiting_deposit",
      case_count: 3,
      sla_breached_count: 1,
      update_overdue_count: 2,
      oldest_in_state_days: 12,
    },
  ],
  by_supplier: [
    {
      supplier_name: "Quiet & Sons",
      case_count: 3,
      update_overdue_count: 2,
      escalation_due_count: 1,
      sla_breached_count: 1,
      longest_silence_days: 12,
    },
  ],
};

vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getPortfolioSummary: async () => SUMMARY }),
}));

import ControlTowerPage from "../control-tower/page";

// Vitest runs without globals here, so testing-library cannot clean up on
// its own — every render would otherwise stay in the document.
afterEach(cleanup);

/**
 * Each Control Tower row counts ACTIVE cases only, so its drill-down must ask
 * for exactly that set — a link without `active_only` would list a
 * supplier's closed cases too and no longer match the number it sits beside.
 */
describe("Control Tower drill-down links", () => {
  it("links a state row to that state's active cases, named by the state", async () => {
    render(<ControlTowerPage />);

    // The accessible name starts with the badge's own text (WCAG 2.5.3).
    const link = await screen.findByRole("link", { name: /^Chờ đặt cọc/ });
    expect(link.getAttribute("href")).toBe(
      "/supply-chain/po-cases?state=waiting_deposit&active_only=true",
    );
  });

  it("links a supplier row to that supplier's active cases, name intact", async () => {
    render(<ControlTowerPage />);

    const link = await screen.findByRole("link", { name: "Quiet & Sons" });
    const target = new URL(link.getAttribute("href") ?? "", "http://web.test");
    expect(target.pathname).toBe("/supply-chain/po-cases");
    expect(Object.fromEntries(target.searchParams)).toEqual({
      supplier_name: "Quiet & Sons",
      active_only: "true",
    });
  });
});
