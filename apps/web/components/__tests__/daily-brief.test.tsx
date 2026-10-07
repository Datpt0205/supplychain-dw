import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { BriefGroup, DailyBrief, POCase } from "@dw/contracts";

import { DailyBriefView, groupHeadline } from "../supply-chain/daily-brief";

// No vitest globals here, so testing-library cannot clean up on its own.
afterEach(cleanup);

function poCase(overrides: Partial<POCase> = {}): POCase {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    po_reference: "PO-1",
    supplier_name: "Quiet & Sons",
    state: "waiting_deposit",
    interrupted_state: null,
    created_at: "2026-09-01T00:00:00Z",
    version: 2,
    order_kind: "new",
    product_dev_case_id: null,
    pic_user_id: null,
    category: null,
    ...overrides,
  };
}

function group(overrides: Partial<BriefGroup> = {}): BriefGroup {
  return {
    key: "sla_breached:deposit",
    signal: "sla_breached",
    qualifier: "deposit",
    state: null,
    total: 1,
    product_state: null,
    product_entries: [],
    entries: [
      {
        case: poCase(),
        days: 12,
        limit_days: 10,
        transition: null,
        approval_action: null,
      },
    ],
    ...overrides,
  };
}

function brief(overrides: Partial<DailyBrief> = {}): DailyBrief {
  return {
    generated_at: "2026-09-28T01:00:00Z",
    active_case_count: 5,
    flagged_case_count: 1,
    approvals_visible: true,
    product_cases_visible: true,
    active_product_case_count: 0,
    flagged_product_case_count: 0,
    groups: [group()],
    ...overrides,
  };
}

describe("groupHeadline", () => {
  it("names the SLA milestone in words", () => {
    expect(groupHeadline(group({ total: 3 }))).toBe("3 PO quá SLA đặt cọc");
  });

  it("falls back to a milestone's own name when it has no label", () => {
    expect(
      groupHeadline(group({ key: "sla_breached:bm05", qualifier: "bm05" })),
    ).toBe("1 PO quá SLA bm05");
  });

  it("names the state a waiting-on-us group is defined by", () => {
    expect(
      groupHeadline(
        group({
          key: "waiting_on_us:waiting_payment",
          signal: "waiting_on_us",
          qualifier: "waiting_payment",
          state: "waiting_payment",
          total: 6,
        }),
      ),
    ).toBe("6 case chờ thanh toán");
  });

  it("names the cases awaiting their PO in the glossary's words, PO kept whole", () => {
    expect(
      groupHeadline(
        group({
          key: "waiting_on_us:order_requested",
          signal: "waiting_on_us",
          qualifier: "order_requested",
          state: "order_requested",
          total: 2,
        }),
      ),
    ).toBe("2 case chờ tạo PO");
  });
});

describe("DailyBriefView", () => {
  it("says approvals were not looked at, rather than implying none are pending", () => {
    render(<DailyBriefView brief={brief({ approvals_visible: false })} />);
    expect(screen.getByText(/không có quyền xem hộp phê duyệt/)).toBeTruthy();
  });

  it("says nothing about approvals when it did look", () => {
    render(<DailyBriefView brief={brief()} />);
    expect(screen.queryByText(/không có quyền xem hộp phê duyệt/)).toBeNull();
  });

  it("links a state-defined group to the case list filtered to that state", () => {
    render(
      <DailyBriefView
        brief={brief({
          groups: [
            group({
              key: "case_blocked",
              signal: "case_blocked",
              qualifier: null,
              state: "blocked",
            }),
          ],
        })}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Mở danh sách" }).getAttribute("href"),
    ).toBe("/supply-chain/po-cases?state=blocked&active_only=true");
  });

  it("links an SLA group to the Attention Queue and approvals to the inbox", () => {
    render(
      <DailyBriefView
        brief={brief({
          groups: [
            group(),
            group({
              key: "approval_pending",
              signal: "approval_pending",
              qualifier: null,
              entries: [
                {
                  case: poCase({ id: "22222222-2222-4222-8222-222222222222" }),
                  days: 2,
                  limit_days: null,
                  transition: null,
                  approval_action: "cancel",
                },
              ],
            }),
          ],
        })}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Mở Cần chú ý" }).getAttribute("href"),
    ).toBe("/supply-chain/attention-queue");
    expect(
      screen
        .getByRole("link", { name: "Mở hộp phê duyệt" })
        .getAttribute("href"),
    ).toBe("/approvals");
  });

  it("gives a reported delay no list link, since no page lists that signal", () => {
    render(
      <DailyBriefView
        brief={brief({
          groups: [
            group({
              key: "supplier_reported_delay",
              signal: "supplier_reported_delay",
              qualifier: null,
            }),
          ],
        })}
      />,
    );
    const card = document.getElementById("brief-supplier_reported_delay")!;
    expect(within(card).getByText("1 case nhà cung cấp báo trễ")).toBeTruthy();
    // Only the case's own link: no group-level link at all.
    expect(
      within(card)
        .getAllByRole("link")
        .map((l) => l.textContent),
    ).toEqual(["PO-1"]);
  });

  it("names a case without its PO number in words, still linked by id", () => {
    render(
      <DailyBriefView
        brief={brief({
          groups: [
            group({
              key: "waiting_on_us:order_requested",
              signal: "waiting_on_us",
              qualifier: "order_requested",
              state: "order_requested",
              entries: [
                {
                  case: poCase({
                    po_reference: null,
                    state: "order_requested",
                  }),
                  days: 1,
                  limit_days: null,
                  transition: null,
                  approval_action: null,
                },
              ],
            }),
          ],
        })}
      />,
    );
    expect(screen.getByText("1 case chờ tạo PO")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Chưa có số PO" }).getAttribute("href"),
    ).toBe("/supply-chain/po-cases/11111111-1111-4111-8111-111111111111");
  });

  it("links every case to its workspace and counts the ones not carried", () => {
    render(
      <DailyBriefView brief={brief({ groups: [group({ total: 14 })] })} />,
    );
    expect(
      screen.getByRole("link", { name: "PO-1" }).getAttribute("href"),
    ).toBe("/supply-chain/po-cases/11111111-1111-4111-8111-111111111111");
    expect(screen.getByText("và 13 case khác.")).toBeTruthy();
    expect(screen.getByText("12 ngày, hạn 10 ngày")).toBeTruthy();
  });

  it("calls a brief with only recent changes one with nothing to handle", () => {
    render(
      <DailyBriefView
        brief={brief({
          flagged_case_count: 0,
          groups: [
            group({
              key: "changed_recently",
              signal: "changed_recently",
              qualifier: null,
              entries: [
                {
                  case: poCase(),
                  days: null,
                  limit_days: null,
                  transition: {
                    from_state: "production",
                    to_state: "qc",
                    reason: null,
                    occurred_at: "2026-09-28T00:00:00Z",
                  },
                  approval_action: null,
                },
              ],
            }),
          ],
        })}
      />,
    );
    expect(screen.getByText(/Không có việc nào cần xử lý/)).toBeTruthy();
    expect(
      screen.getByText("1 case vừa đổi trạng thái trong 24 giờ qua"),
    ).toBeTruthy();
  });
});
