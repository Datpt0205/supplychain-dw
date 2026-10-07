import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BriefGroup, DailyBrief, DailyBriefSummary } from "@dw/contracts";

let workspaceId = "ws-A";
vi.mock("../../../lib/auth/auth-context", () => ({
  useAuth: () => ({ active: { workspaceId } }),
}));

function group(key: string, total: number): BriefGroup {
  const [signal, qualifier] = key.split(":") as [BriefGroup["signal"], string?];
  return {
    key,
    signal,
    qualifier: qualifier ?? null,
    state: null,
    product_state: null,
    total,
    product_entries: [],
    entries: [
      {
        case: {
          id: `11111111-1111-4111-8111-11111111111${total}`,
          po_reference: `PO-${total}`,
          supplier_name: "Quiet & Sons",
          state: "waiting_deposit",
          interrupted_state: null,
          created_at: "2026-09-01T00:00:00Z",
          version: 1,
          order_kind: "new",
          product_dev_case_id: null,
          pic_user_id: null,
          category: null,
        },
        days: 12,
        limit_days: 10,
        transition: null,
        approval_action: null,
      },
    ],
  };
}

function brief(groups: BriefGroup[]): DailyBrief {
  return {
    generated_at: "2026-09-28T01:00:00Z",
    active_case_count: 9,
    flagged_case_count: 3,
    approvals_visible: true,
    product_cases_visible: true,
    active_product_case_count: 0,
    flagged_product_case_count: 0,
    groups,
  };
}

const LOADED = brief([group("sla_breached:deposit", 3)]);
const getDailyBrief = vi.fn(async () => LOADED);
const summarizeDailyBrief = vi.fn();
vi.mock("../../../lib/session", () => ({
  apiClient: () => ({ getDailyBrief, summarizeDailyBrief }),
}));
// The roster is the page's own fetch; these tests are about the brief.
vi.mock("../../../lib/directory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/directory")>()),
  useWorkspaceMembers: () => [],
}));

import DailyBriefPage from "../daily-brief/page";

afterEach(() => {
  // No vitest globals here, so testing-library cannot clean up on its own.
  cleanup();
  summarizeDailyBrief.mockReset();
  workspaceId = "ws-A";
});

function written(overrides: Partial<DailyBriefSummary["summary"]> = {}) {
  const fresh = brief([
    group("sla_breached:deposit", 3),
    group("case_blocked", 1),
  ]);
  return {
    brief: fresh,
    summary: {
      status: "written",
      sentences: [
        {
          text: "Ưu tiên 3 PO quá hạn cọc.",
          group_keys: ["sla_breached:deposit"],
        },
      ],
      dropped: 2,
      ...overrides,
    },
  } satisfies DailyBriefSummary;
}

async function summarize(): Promise<void> {
  fireEvent.click(
    await screen.findByRole("button", { name: "Tóm tắt bằng AI" }),
  );
}

describe("the daily brief's AI summary", () => {
  it("is not asked for until the button is pressed", async () => {
    render(<DailyBriefPage />);
    expect(await screen.findByText("3 PO quá SLA đặt cọc")).toBeTruthy();
    expect(summarizeDailyBrief).not.toHaveBeenCalled();
  });

  it("shows the kept sentences as AI-written, linked to the groups they cite", async () => {
    summarizeDailyBrief.mockResolvedValue(written());
    render(<DailyBriefPage />);
    await summarize();

    const panel = await screen.findByRole("region", {
      name: "Tóm tắt do AI viết",
    });
    expect(within(panel).getByText("Ưu tiên 3 PO quá hạn cọc.")).toBeTruthy();
    const chip = within(panel).getByRole("link", {
      name: "3 PO quá SLA đặt cọc",
    });
    expect(chip.getAttribute("href")).toBe("#brief-sla_breached:deposit");
    expect(
      within(panel).getByText(
        "2 câu khác bị loại vì không khớp với dữ liệu bản tin.",
      ),
    ).toBeTruthy();
  });

  it("shows the brief the summary was checked against, not the older copy", async () => {
    summarizeDailyBrief.mockResolvedValue(written());
    render(<DailyBriefPage />);
    expect(screen.queryByText("1 case đang bị chặn")).toBeNull();
    await summarize();
    expect(await screen.findByText("1 case đang bị chặn")).toBeTruthy();
  });

  it("says when no sentence survived, rather than showing nothing", async () => {
    summarizeDailyBrief.mockResolvedValue(
      written({ status: "nothing_kept", sentences: [], dropped: 3 }),
    );
    render(<DailyBriefPage />);
    await summarize();
    expect(
      await screen.findByText(/AI đã viết 3 câu nhưng không câu nào khớp/),
    ).toBeTruthy();
  });

  it("announces a failed summary as an alert and keeps the brief", async () => {
    summarizeDailyBrief.mockRejectedValue(new Error("Hết hạn mức chi tiêu"));
    render(<DailyBriefPage />);
    await summarize();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Hết hạn mức chi tiêu",
    );
    expect(screen.getByText("3 PO quá SLA đặt cọc")).toBeTruthy();
  });

  it("drops a summary when the workspace changes", async () => {
    summarizeDailyBrief.mockResolvedValue(written());
    const { rerender } = render(<DailyBriefPage />);
    await summarize();
    await screen.findByRole("region", { name: "Tóm tắt do AI viết" });

    workspaceId = "ws-B";
    await act(async () => rerender(<DailyBriefPage />));

    expect(
      screen.queryByRole("region", { name: "Tóm tắt do AI viết" }),
    ).toBeNull();
  });
});
