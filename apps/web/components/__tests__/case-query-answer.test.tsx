import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { AIWorkResponse, POCase } from "@dw/contracts";

import { CaseQueryAnswer } from "../supply-chain/case-query-answer";

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
    ...overrides,
  };
}

function answer(overrides: Partial<AIWorkResponse> = {}): AIWorkResponse {
  return {
    intent: "list_cases",
    outcome: "list",
    understood: {
      state: "waiting_deposit",
      supplier_name: "Quiet & Sons",
      active_only: true,
      po_reference: null,
    },
    citations: [
      { field: "supplier", quote: "quiet & sons" },
      { field: "state", quote: "chờ đặt cọc" },
      { field: "active_only", quote: "đang chạy" },
    ],
    ignored_fields: [],
    unusable_fields: [],
    candidates: [],
    data_view: { type: "case_table", rows: [poCase()], has_more: false },
    ...overrides,
  };
}

/** Every link the answer renders — the property that matters is where each
 * one points, since none may come from text a model wrote. */
function hrefs(container: HTMLElement): string[] {
  return [...container.querySelectorAll("a")].map(
    (a) => a.getAttribute("href") ?? "",
  );
}

describe("CaseQueryAnswer", () => {
  it("shows what it understood, from which words, and links built by code", () => {
    const { container } = render(<CaseQueryAnswer answer={answer()} />);

    expect(screen.getByText("1 case khớp câu hỏi.")).toBeTruthy();
    expect(screen.getByText(/Nhà cung cấp: Quiet & Sons/)).toBeTruthy();
    expect(screen.getByText(/từ “chờ đặt cọc”/)).toBeTruthy();
    const full = new URL(
      screen
        .getByRole("link", { name: "Mở trong danh sách PO cases" })
        .getAttribute("href") ?? "",
      "http://web.test",
    );
    expect(Object.fromEntries(full.searchParams)).toEqual({
      state: "waiting_deposit",
      supplier_name: "Quiet & Sons",
      active_only: "true",
    });
    expect(hrefs(container)).toContain(
      "/supply-chain/po-cases/11111111-1111-4111-8111-111111111111",
    );
  });

  it("offers the full list when more matched than the answer carries", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          data_view: { type: "case_table", rows: [poCase()], has_more: true },
        })}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Xem tất cả case khớp" }),
    ).toBeTruthy();
  });

  it("opens one named case through its id", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          intent: "open_case",
          outcome: "open",
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: "PO-1",
          },
          citations: [{ field: "po_reference", quote: "PO-1" }],
          data_view: { type: "case_link", case: poCase() },
        })}
      />,
    );
    expect(
      screen.getByRole("link", { name: /Mở case PO-1/ }).getAttribute("href"),
    ).toBe("/supply-chain/po-cases/11111111-1111-4111-8111-111111111111");
  });

  it("lets a person pick among ambiguous suppliers, never picks for them", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          outcome: "supplier_ambiguous",
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [{ field: "supplier", quote: "Elmich" }],
          candidates: ["Elmich Co.", "Elmich Việt Nam"],
          data_view: null,
        })}
      />,
    );
    expect(
      screen.getByText("“Elmich” khớp nhiều nhà cung cấp — chọn một:"),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: "Elmich Việt Nam" })
        .getAttribute("href"),
    ).toBe("/supply-chain/po-cases?supplier_name=Elmich+Vi%E1%BB%87t+Nam");
  });

  it("says which part of the question it found no grounds for", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          outcome: "not_understood",
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [],
          ignored_fields: ["state"],
          data_view: null,
        })}
      />,
    );
    expect(
      screen.getByText(
        "Chưa hiểu câu hỏi: không tìm thấy căn cứ trong câu hỏi cho trạng thái.",
      ),
    ).toBeTruthy();
  });

  it("renders question text and stored names as characters, never markup or links", () => {
    const hostile = '<img src=x onerror="alert(1)">javascript:alert(1)';
    const { container } = render(
      <CaseQueryAnswer
        answer={answer({
          understood: {
            state: null,
            supplier_name: hostile,
            active_only: false,
            po_reference: null,
          },
          citations: [{ field: "supplier", quote: hostile }],
          data_view: {
            type: "case_table",
            rows: [poCase({ supplier_name: hostile, po_reference: hostile })],
            has_more: false,
          },
        })}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    for (const href of hrefs(container)) {
      expect(href.startsWith("/supply-chain/")).toBe(true);
    }
  });
  it("keeps the question's state and active-only on every candidate link", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          outcome: "supplier_ambiguous",
          understood: {
            state: "waiting_deposit",
            supplier_name: null,
            active_only: true,
            po_reference: null,
          },
          citations: [{ field: "supplier", quote: "Elmich" }],
          candidates: ["Elmich Co.", "Elmich Việt Nam"],
          data_view: null,
        })}
      />,
    );
    const target = new URL(
      screen.getByRole("link", { name: "Elmich Co." }).getAttribute("href") ??
        "",
      "http://web.test",
    );
    expect(Object.fromEntries(target.searchParams)).toEqual({
      supplier_name: "Elmich Co.",
      state: "waiting_deposit",
      active_only: "true",
    });
  });

  it("lists at most ten candidates and says how many more there are", () => {
    const candidates = Array.from({ length: 13 }, (_, i) => `Co ${i + 1}`);
    render(
      <CaseQueryAnswer
        answer={answer({
          outcome: "supplier_ambiguous",
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [{ field: "supplier", quote: "Co" }],
          candidates,
          data_view: null,
        })}
      />,
    );
    expect(screen.getAllByRole("link")).toHaveLength(10);
    expect(screen.getByText(/và 3 nhà cung cấp khác/)).toBeTruthy();
  });

  it("never calls unfiltered rows a match for the question", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [],
        })}
      />,
    );
    expect(
      screen.getByText("Chưa áp dụng bộ lọc nào — đây là 1 case mới nhất."),
    ).toBeTruthy();
    expect(screen.queryByText(/khớp câu hỏi/)).toBeNull();
  });
  it("says a stated-but-unusable field was stated, not that it lacked grounds", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          outcome: "not_understood",
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [{ field: "po_reference", quote: "PO-123" }],
          ignored_fields: [],
          unusable_fields: ["po_reference"],
          data_view: null,
        })}
      />,
    );
    const sentence = screen.getByText(/Chưa hiểu câu hỏi/).textContent ?? "";
    expect(sentence).toContain("câu hỏi có nêu mã PO");
    expect(sentence).not.toContain("không tìm thấy căn cứ");
  });

  it("offers the whole list, not 'every match', when no filter applied", () => {
    render(
      <CaseQueryAnswer
        answer={answer({
          understood: {
            state: null,
            supplier_name: null,
            active_only: false,
            po_reference: null,
          },
          citations: [],
          data_view: { type: "case_table", rows: [poCase()], has_more: true },
        })}
      />,
    );
    expect(
      screen.getByRole("link", { name: "Xem tất cả PO cases" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: "Xem tất cả case khớp" }),
    ).toBeNull();
  });
});
