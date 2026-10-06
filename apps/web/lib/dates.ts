/**
 * The one place a date is turned into text for the screen (ui-quality §8).
 *
 * Every time is shown in Vietnam's zone, whatever the browser's own, time
 * first and 24-hour, the year always written: "09:00 14/10/2026" (the E-HSDT
 * v3 handoff's order). It carries "giờ Việt Nam" on the value
 * (`formatDateTimeFull`) or once in the column header that holds bare cells
 * (`formatDateTime`, with `VN_TIME` in the header). Written out rather than
 * left to a locale, because the order, the separator and the clock are the
 * requirement, not a locale preference.
 */

const ZONE = "Asia/Ho_Chi_Minh";

/** The zone's name as a screen writes it. */
export const VN_TIME = "giờ Việt Nam";

const parts = new Intl.DateTimeFormat("en-GB", {
  timeZone: ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function fields(iso: string): Record<string, string> {
  return Object.fromEntries(
    parts.formatToParts(new Date(iso)).map((part) => [part.type, part.value]),
  );
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * "DD/MM/YYYY" in Vietnam, or an em dash when there is no date. A date-only
 * value is a calendar day and is printed as written, never through a
 * midnight that another zone would move.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const day = DATE_ONLY.exec(iso);
  if (day) return `${day[3]}/${day[2]}/${day[1]}`;
  const p = fields(iso);
  return `${p.day}/${p.month}/${p.year}`;
}

/** "HH:mm DD/MM/YYYY" in Vietnam, for a cell whose header says "giờ Việt
 * Nam"; an em dash when there is no time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const p = fields(iso);
  return `${p.hour}:${p.minute} ${p.day}/${p.month}/${p.year}`;
}

/** "HH:mm DD/MM/YYYY (giờ Việt Nam)": a time standing on its own. */
export function formatDateTimeFull(iso: string | null | undefined): string {
  if (!iso) return "—";
  return `${formatDateTime(iso)} (${VN_TIME})`;
}
