import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatDateTimeFull } from "../dates";

describe("lib/dates (ui-quality §8)", () => {
  it("writes time first, in Vietnam, whatever the browser's zone", () => {
    // 02:00 UTC is 09:00 in Hà Nội; the test runner's own zone plays no part.
    expect(formatDateTime("2026-10-14T02:00:00Z")).toBe("09:00 14/10/2026");
    expect(formatDateTimeFull("2026-10-14T02:00:00Z")).toBe(
      "09:00 14/10/2026 (giờ Việt Nam)",
    );
  });

  it("crosses midnight by Vietnam's clock, not UTC's", () => {
    expect(formatDateTime("2026-10-13T18:30:00Z")).toBe("01:30 14/10/2026");
    expect(formatDate("2026-10-13T18:30:00Z")).toBe("14/10/2026");
  });

  it("prints a date-only value as the calendar day it names", () => {
    expect(formatDate("2026-10-14")).toBe("14/10/2026");
  });

  it("says there is no time rather than inventing one", () => {
    expect(formatDateTime(null)).toBe("—");
    expect(formatDateTimeFull(undefined)).toBe("—");
  });
});
