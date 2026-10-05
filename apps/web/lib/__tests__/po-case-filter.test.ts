import { describe, expect, it } from "vitest";
import {
  poCasesHref,
  readPOCaseFilter,
  type ListFilter,
} from "../supply-chain/po-case-filter";

/** What a URL written by `poCasesHref` reads back as. */
function roundTrip(filter: ListFilter): ListFilter {
  const href = poCasesHref(filter);
  return readPOCaseFilter(new URL(href, "http://web.test").searchParams);
}

describe("PO case list filter in the URL", () => {
  it("reads back exactly what a drill-down link wrote", () => {
    const filter: ListFilter = {
      state: "waiting_deposit",
      supplierName: "Quiet & Sons + Co #2 — Nhà Máy Đồng Nai",
      activeOnly: true,
    };
    expect(roundTrip(filter)).toEqual(filter);
  });

  it("writes nothing for a filter left unset", () => {
    expect(poCasesHref({ activeOnly: false })).toBe("/supply-chain/po-cases");
  });

  it("ignores a state the server would refuse instead of sending it", () => {
    const filter = readPOCaseFilter(
      new URLSearchParams("state=not_a_state&active_only=true"),
    );
    expect(filter).toEqual({
      state: undefined,
      supplierName: undefined,
      activeOnly: true,
    });
  });

  it("treats only the literal true as active-only", () => {
    for (const value of ["1", "yes", "TRUE", ""]) {
      expect(
        readPOCaseFilter(new URLSearchParams(`active_only=${value}`))
          .activeOnly,
      ).toBe(false);
    }
  });

  it("treats an empty supplier name as no supplier filter", () => {
    expect(
      readPOCaseFilter(new URLSearchParams("supplier_name=")).supplierName,
    ).toBeUndefined();
  });
});
