import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type {
  AIWorkResponse,
  BriefGroup,
  DailyBrief,
  ProductCaseRef,
} from "@dw/contracts";

import { CaseQueryAnswer } from "../supply-chain/case-query-answer";
import { DailyBriefView, groupHeadline } from "../supply-chain/daily-brief";
import {
  productCasesHref,
  readProductCaseFilter,
} from "../../lib/supply-chain/product-case-filter";

// Stage-1 ticket 08: product-development cases in the daily brief and in the
// command bar's answers. No vitest globals here, so clean up by hand.
afterEach(cleanup);

const LAN = "22222222-2222-4222-8222-222222222222";

function ref(overrides: Partial<ProductCaseRef> = {}): ProductCaseRef {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    proposal_code: "SP-028",
    product_name: "Chảo chống dính 28cm",
    category: "chao",
    pic_user_id: LAN,
    state: "pending_bod_review",
    ...overrides,
  };
}

function productGroup(overrides: Partial<BriefGroup> = {}): BriefGroup {
  return {
    key: "product_awaiting_bod",
    signal: "product_awaiting_bod",
    qualifier: null,
    state: null,
    product_state: "pending_bod_review",
    total: 1,
    entries: [],
    product_entries: [
      {
        case: ref(),
        days: 3,
        limit_days: null,
        round_no: null,
        sample_result: null,
      },
    ],
    ...overrides,
  };
}

function brief(overrides: Partial<DailyBrief> = {}): DailyBrief {
  return {
    generated_at: "2026-10-07T10:00:00Z",
    active_case_count: 0,
    flagged_case_count: 0,
    approvals_visible: true,
    product_cases_visible: true,
    active_product_case_count: 4,
    flagged_product_case_count: 1,
    groups: [productGroup()],
    ...overrides,
  };
}

describe("stage-1 groups in the daily brief", () => {
  it("names each stage-1 group in words", () => {
    expect(groupHeadline(productGroup({ total: 2 }))).toBe(
      "2 hồ sơ phát triển chờ BGĐ duyệt",
    );
    expect(
      groupHeadline(
        productGroup({
          key: "sample_evaluated_today:needs_revision",
          signal: "sample_evaluated_today",
          qualifier: "needs_revision",
          product_state: null,
          total: 3,
        }),
      ),
    ).toBe("3 mẫu đánh giá hôm nay: Cần chỉnh sửa");
    expect(
      groupHeadline(
        productGroup({
          key: "product_sla_breached:sample_testing",
          signal: "product_sla_breached",
          qualifier: "sample_testing",
          product_state: null,
        }),
      ),
    ).toBe("1 hồ sơ phát triển quá hạn test mẫu");
  });

  it("links each product case to its page and the group to its filtered list", () => {
    render(<DailyBriefView brief={brief()} />);
    expect(
      screen.getByRole("link", { name: "SP-028" }).getAttribute("href"),
    ).toBe("/supply-chain/product-cases/33333333-3333-4333-8333-333333333333");
    expect(
      screen.getByRole("link", { name: "Mở danh sách" }).getAttribute("href"),
    ).toBe("/supply-chain/product-cases?state=pending_bod_review");
    expect(screen.getByText("chờ 3 ngày")).toBeTruthy();
    expect(screen.getByText(/1 hồ sơ phát triển cần xử lý/)).toBeTruthy();
  });

  it("names the PIC of each sample evaluated today", () => {
    render(
      <DailyBriefView
        brief={brief({
          groups: [
            productGroup({
              key: "sample_evaluated_today:passed",
              signal: "sample_evaluated_today",
              qualifier: "passed",
              product_state: null,
              product_entries: [
                {
                  case: ref({ state: "pending_bod_review" }),
                  days: null,
                  limit_days: null,
                  round_no: 2,
                  sample_result: "passed",
                },
              ],
            }),
          ],
        })}
        members={[
          {
            user_id: LAN,
            display_name: "Nguyễn Thị Lan",
            email: null,
            role_keys: [],
            permission_set_keys: [],
            department: "",
          },
        ]}
      />,
    );
    const card = screen
      .getByText("1 mẫu đánh giá hôm nay: Đạt")
      .closest(".ant-card");
    expect(card).toBeTruthy();
    const scope = within(card as HTMLElement);
    expect(scope.getByText("PIC: Nguyễn Thị Lan")).toBeTruthy();
    expect(scope.getByText("vòng mẫu 2")).toBeTruthy();
  });

  it("says product cases were not looked at, rather than that there are none", () => {
    render(
      <DailyBriefView
        brief={brief({ product_cases_visible: false, groups: [] })}
      />,
    );
    expect(
      screen.getByText(/không có quyền xem hồ sơ phát triển/),
    ).toBeTruthy();
  });

  it("renders a typed product name as text, never markup", () => {
    const hostile = '<img src=x onerror="alert(1)"> Nồi';
    const { container } = render(
      <DailyBriefView
        brief={brief({
          groups: [
            productGroup({
              product_entries: [
                {
                  case: ref({ product_name: hostile }),
                  days: 1,
                  limit_days: null,
                  round_no: null,
                  sample_result: null,
                },
              ],
            }),
          ],
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(hostile)).toBeTruthy();
  });
});

function answer(overrides: Partial<AIWorkResponse> = {}): AIWorkResponse {
  return {
    intent: "list_product_cases",
    outcome: "product_list",
    understood: {
      state: null,
      supplier_name: null,
      active_only: false,
      po_reference: null,
      product_state: "sample_testing",
      category: "chao",
      pic_user_id: LAN,
      proposal_code: null,
    },
    citations: [
      { field: "category", quote: "Chảo" },
      { field: "pic", quote: "Lan" },
      { field: "product_state", quote: "đang test mẫu" },
    ],
    ignored_fields: [],
    unusable_fields: [],
    candidates: [],
    data_view: {
      type: "product_case_table",
      rows: [ref({ state: "sample_testing" })],
      has_more: true,
    },
    ...overrides,
  };
}

describe("product cases in the command bar", () => {
  it("lists product cases and links the rest with the same filter, built by code", () => {
    render(<CaseQueryAnswer answer={answer()} />);
    expect(
      screen.getByText("Hiển thị 1 hồ sơ đầu tiên khớp câu hỏi."),
    ).toBeTruthy();
    expect(screen.getByText(/Category \(từ “Chảo”\)/)).toBeTruthy();
    expect(screen.getByText(/Người phụ trách \(từ “Lan”\)/)).toBeTruthy();
    const full = new URL(
      screen
        .getByRole("link", { name: "Xem tất cả hồ sơ khớp" })
        .getAttribute("href") ?? "",
      "http://web.test",
    );
    expect(full.pathname).toBe("/supply-chain/product-cases");
    expect(Object.fromEntries(full.searchParams)).toEqual({
      state: "sample_testing",
      pic: LAN,
      category: "chao",
    });
  });

  it("opens one named product case through its id", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          intent: "open_product_case",
          outcome: "product_open",
          understood: {
            ...answer().understood,
            product_state: null,
            category: null,
            pic_user_id: null,
            proposal_code: "SP-028",
          },
          citations: [{ field: "proposal_code", quote: "SP-028" }],
          data_view: { type: "product_case_link", case: ref() },
        })}
      />,
    );
    expect(
      screen
        .getByRole("link", { name: "Mở hồ sơ SP-028" })
        .getAttribute("href"),
    ).toBe("/supply-chain/product-cases/33333333-3333-4333-8333-333333333333");
  });

  it("says a code not found is not found, whoever owns it", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          intent: "open_product_case",
          outcome: "product_not_found",
          understood: {
            ...answer().understood,
            product_state: null,
            category: null,
            pic_user_id: null,
            proposal_code: "SP-777",
          },
          data_view: null,
        })}
      />,
    );
    expect(
      screen.getByText("Không tìm thấy hồ sơ phát triển “SP-777”."),
    ).toBeTruthy();
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("asks for a code rather than opening anything", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          intent: "open_product_case",
          outcome: "proposal_code_missing",
          understood: {
            ...answer().understood,
            product_state: null,
            category: null,
            pic_user_id: null,
          },
          citations: [],
          data_view: null,
        })}
      />,
    );
    expect(screen.getByText("Câu hỏi chưa nêu mã đề xuất nào.")).toBeTruthy();
  });
});

describe("product case list filter in the URL", () => {
  it("round-trips every field and drops an unknown state", () => {
    const href = productCasesHref({
      state: "pending_signoff",
      mine: true,
      pic: LAN,
      category: "noi",
    });
    const params = new URL(href, "http://web.test").searchParams;
    expect(readProductCaseFilter(params)).toEqual({
      state: "pending_signoff",
      mine: true,
      pic: LAN,
      category: "noi",
    });
    expect(
      readProductCaseFilter(new URLSearchParams("state=shipped")).state,
    ).toBeUndefined();
    expect(productCasesHref({})).toBe("/supply-chain/product-cases");
  });
});
