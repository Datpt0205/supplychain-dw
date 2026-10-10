import { describe, expect, it } from "vitest";
import { fold, matches } from "../search";

describe("lib/search (ui-quality §8)", () => {
  it("finds marked words from unmarked ones, and đ from d", () => {
    expect(fold("Nồi Inox ĐA NĂNG")).toBe("noi inox da nang");
    expect(matches("noi inox", ["Nồi inox 3 đáy 24cm"])).toBe(true);
    expect(matches("dap", ["Đáp ứng"])).toBe(true);
  });

  it("needs every word, in any field", () => {
    expect(matches("sunhouse 007", ["PO-2026-007", "Sunhouse"])).toBe(true);
    expect(matches("sunhouse 008", ["PO-2026-007", "Sunhouse"])).toBe(false);
  });

  it("matches everything on an empty query", () => {
    expect(matches("  ", ["x"])).toBe(true);
  });
});
