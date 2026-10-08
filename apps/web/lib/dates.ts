import dayjs from "dayjs";
import timezone from "dayjs/plugin/timezone";
import utc from "dayjs/plugin/utc";

/**
 * The one place a date is turned into text for the screen (ui-quality §8).
 *
 * Every instant is shown in Vietnam's time, whatever the viewer's machine is
 * set to: a deadline read in Los Angeles must name the same day it names in
 * Hà Nội. Time first and 24-hour, the year always written: "09:00 14/10/2026"
 * (the E-HSDT v3 handoff's order). It carries "giờ Việt Nam" on the value
 * (`formatDateTimeFull`) or once in the column header that holds bare cells
 * (`formatDateTime`, with `VN_TIME` in the header). Written out rather than
 * left to a locale, because the order, the separator and the clock are the
 * requirement, not a locale preference.
 */

dayjs.extend(utc);
dayjs.extend(timezone);

export const VN_TIME_ZONE = "Asia/Ho_Chi_Minh";

/** The zone's name as a screen writes it. */
export const VN_TIME = "giờ Việt Nam";

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

function inVietnam(iso: string) {
  return dayjs.utc(iso).tz(VN_TIME_ZONE);
}

/**
 * "DD/MM/YYYY" in Vietnam's calendar, or an em dash when there is no date. A
 * date-only value is a calendar day and is printed as written, never through
 * a midnight that another zone would move.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  if (DATE_ONLY.test(iso)) return dayjs(iso, "YYYY-MM-DD").format("DD/MM/YYYY");
  return inVietnam(iso).format("DD/MM/YYYY");
}

/** "HH:mm DD/MM/YYYY" in Vietnam, for a cell whose header says "giờ Việt
 * Nam"; an em dash when there is no time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return inVietnam(iso).format("HH:mm DD/MM/YYYY");
}

/** "HH:mm DD/MM/YYYY (giờ Việt Nam)": a time standing on its own. */
export function formatDateTimeFull(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${formatDateTime(iso)} (${VN_TIME})`;
}
