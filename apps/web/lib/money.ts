/**
 * The one place an amount of money becomes text, and text becomes an amount.
 *
 * Vietnamese dong, whole numbers only: `18.450.000.000 đ`, never "₫" and never
 * a locale's guess. Written out rather than left to `Intl`, because the dot as
 * the thousands separator and "đ" after a no-break space are the requirement,
 * not a browser's preference. A context shows money through these functions;
 * it does not format a second way.
 */

/** No-break space: "đ" never wraps onto a line of its own. */
const NBSP = " ";

function grouped(amount: number): string {
  const digits = String(Math.abs(Math.trunc(amount)));
  const withDots = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return amount < 0 ? `-${withDots}` : withDots;
}

/** `18.450.000.000 đ`. */
export function formatMoney(amount: number): string {
  return `${grouped(amount)}${NBSP}đ`;
}

/** `18.450.000.000`, for a field that shows "đ" as its own suffix. */
export function formatMoneyNumber(amount: number): string {
  return grouped(amount);
}

/** `1.234`: a count, grouped the same way as money. */
export function formatCount(n: number): string {
  return grouped(n);
}

/**
 * `18,45 tỷ`. Not for a value being compared with a threshold or being
 * corrected: 14.995.000.000 and 15.000.000.000 both read "15 tỷ".
 */
export function formatMoneyShort(amount: number): string {
  const billions = Math.round((amount / 1_000_000_000) * 100) / 100;
  return `${String(billions).replace(".", ",")}${NBSP}tỷ`;
}

export type ParsedMoney =
  | { kind: "empty" }
  | { kind: "ok"; value: number }
  | { kind: "invalid"; reason: string };

/**
 * An amount a person typed or pasted. Both thousands styles are read
 * (`18.450.000.000`, `18,450,000,000`), with or without "đ" and spaces. A
 * decimal (`1.5`, `1,5`, `12.345,67`) is refused rather than read as a
 * thousand times the amount.
 */
export function parseMoney(text: string): ParsedMoney {
  const bare = text.replace(/[\s ]+/g, "").replace(/(đ|₫)$/i, "");
  if (bare === "") return { kind: "empty" };
  const whole = /^-?\d+$/.test(bare);
  const dotted = /^-?\d{1,3}(\.\d{3})+$/.test(bare);
  const commaed = /^-?\d{1,3}(,\d{3})+$/.test(bare);
  if (!whole && !dotted && !commaed) {
    return { kind: "invalid", reason: "Số tiền phải là số nguyên" };
  }
  const value = Number(bare.replace(/[.,]/g, ""));
  if (!Number.isSafeInteger(value)) {
    return { kind: "invalid", reason: "Số tiền vượt giới hạn" };
  }
  return { kind: "ok", value };
}

/** For antd `InputNumber` (`precision={0}`): shows `18.450.000.000`. */
export function moneyInputFormatter(
  value: number | string | undefined,
): string {
  if (value === undefined || value === "") return "";
  const parsed = parseMoney(String(value));
  return parsed.kind === "ok" ? formatMoneyNumber(parsed.value) : String(value);
}

/** For antd `InputNumber`: reads what `moneyInputFormatter` shows, or a paste. */
export function moneyInputParser(text: string | undefined): number {
  const parsed = parseMoney(text ?? "");
  return parsed.kind === "ok" ? parsed.value : NaN;
}

const DIGITS = [
  "không",
  "một",
  "hai",
  "ba",
  "bốn",
  "năm",
  "sáu",
  "bảy",
  "tám",
  "chín",
];
const SCALES = ["", "nghìn", "triệu"];

/** One group of three digits; `full` when a higher group precedes it. */
function readTriple(n: number, full: boolean): string {
  const hundreds = Math.floor(n / 100);
  const tens = Math.floor((n % 100) / 10);
  const units = n % 10;
  const words: string[] = [];
  if (full || hundreds > 0) words.push(DIGITS[hundreds]!, "trăm");
  if (tens === 0) {
    if (units > 0 && words.length > 0) words.push("linh");
  } else if (tens === 1) {
    words.push("mười");
  } else {
    words.push(DIGITS[tens]!, "mươi");
  }
  if (units > 0) {
    if (units === 1 && tens >= 2) words.push("mốt");
    else if (units === 5 && tens >= 1) words.push("lăm");
    else words.push(DIGITS[units]!);
  }
  return words.join(" ");
}

/** Below a billion; `full` when a higher part was already read. */
function readUnderBillion(n: number, full: boolean): string {
  const groups = [n % 1000, Math.floor(n / 1000) % 1000, Math.floor(n / 1e6)];
  const parts: string[] = [];
  for (let at = 2; at >= 0; at -= 1) {
    const group = groups[at]!;
    if (group === 0) continue;
    const triple = readTriple(group, full || parts.length > 0);
    parts.push(SCALES[at] ? `${triple} ${SCALES[at]}` : triple);
  }
  return parts.join(" ");
}

/** Billions repeat the scale: 1.234.000.000.000 is "một nghìn hai trăm ba
 * mươi bốn tỷ", not "một nghìn tỷ hai trăm ba mươi bốn tỷ". */
function readNumber(n: number, full: boolean): string {
  if (n < 1e9) return readUnderBillion(n, full);
  const billions = Math.floor(n / 1e9);
  const rest = n % 1e9;
  const head = `${readNumber(billions, full)} tỷ`;
  return rest > 0 ? `${head} ${readUnderBillion(rest, true)}` : head;
}

/**
 * "Bằng chữ": `Mười tám tỷ bốn trăm năm mươi triệu đồng` for 18.450.000.000.
 * The wording of a legal document stays its context's to decide; this is the
 * reading a form prints under an amount.
 */
export function moneyInWords(amount: number): string {
  if (!Number.isSafeInteger(amount)) {
    throw new RangeError("Số tiền phải là số nguyên trong giới hạn");
  }
  if (amount === 0) return "Không đồng";
  const text = `${amount < 0 ? "âm " : ""}${readNumber(Math.abs(amount), false)} đồng`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}
