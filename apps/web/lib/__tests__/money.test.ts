import { describe, expect, it } from "vitest";
import {
  formatCount,
  formatMoney,
  formatMoneyNumber,
  formatMoneyShort,
  formatPrice,
  moneyInWords,
  moneyInputFormatter,
  moneyInputParser,
  parseMoney,
} from "../money";

const NBSP = "\u00a0";

describe("formatMoney", () => {
  it("groups with dots and ends in đ after a no-break space, never ₫", () => {
    expect(formatMoney(1285000)).toBe(`1.285.000${NBSP}đ`);
    expect(formatMoney(18450000000)).toBe(`18.450.000.000${NBSP}đ`);
    expect(formatMoney(0)).toBe(`0${NBSP}đ`);
    expect(formatMoney(-5000)).toBe(`-5.000${NBSP}đ`);
    expect(formatMoney(999)).toBe(`999${NBSP}đ`);
    expect(formatMoney(18450000000)).not.toContain("₫");
    expect(formatMoneyNumber(18450000000)).toBe("18.450.000.000");
    expect(formatCount(1234)).toBe("1.234");
  });

  it("shortens to tỷ, and is no good for a threshold", () => {
    expect(formatMoneyShort(18450000000)).toBe(`18,45${NBSP}tỷ`);
    expect(formatMoneyShort(14995000000)).toBe(formatMoneyShort(15000000000));
  });
});

describe("parseMoney", () => {
  it.each([
    "18.450.000.000",
    "18,450,000,000",
    "18450000000 đ",
    "18.450.000.000đ",
    " 18 450 000 000 ",
  ])("reads %j", (text) => {
    expect(parseMoney(text)).toEqual({ kind: "ok", value: 18450000000 });
  });

  it.each(["1.5", "1,5", "12.345,67", "12a", "9007199254740993"])(
    "refuses %j instead of guessing",
    (text) => {
      expect(parseMoney(text).kind).toBe("invalid");
    },
  );

  it("says why, without blaming", () => {
    expect(parseMoney("1.5")).toEqual({
      kind: "invalid",
      reason: "Số tiền phải là số nguyên",
    });
    expect(parseMoney("9007199254740993")).toEqual({
      kind: "invalid",
      reason: "Số tiền vượt giới hạn",
    });
  });

  it("tells empty from invalid", () => {
    expect(parseMoney("")).toEqual({ kind: "empty" });
    expect(parseMoney("   ")).toEqual({ kind: "empty" });
  });

  it("round-trips through the InputNumber helpers", () => {
    expect(moneyInputFormatter(18450000000)).toBe("18.450.000.000");
    expect(moneyInputParser("18,450,000,000")).toBe(18450000000);
    expect(Number.isNaN(moneyInputParser("1.5"))).toBe(true);
  });
});

describe("moneyInWords (Bằng chữ)", () => {
  it.each([
    [0, "Không đồng"],
    [5, "Năm đồng"],
    [15, "Mười lăm đồng"],
    [21, "Hai mươi mốt đồng"],
    [105, "Một trăm linh năm đồng"],
    [1050, "Một nghìn không trăm năm mươi đồng"],
    [1005000, "Một triệu không trăm linh năm nghìn đồng"],
    [1285000, "Một triệu hai trăm tám mươi lăm nghìn đồng"],
    [18450000000, "Mười tám tỷ bốn trăm năm mươi triệu đồng"],
    [1000000000000, "Một nghìn tỷ đồng"],
    [1234000000000, "Một nghìn hai trăm ba mươi bốn tỷ đồng"],
    [2000000005, "Hai tỷ không trăm linh năm đồng"],
    [-5000, "Âm năm nghìn đồng"],
  ])("%d reads %j", (amount, words) => {
    expect(moneyInWords(amount)).toBe(words);
  });

  it("refuses a fraction rather than rounding it", () => {
    expect(() => moneyInWords(1.5)).toThrow(RangeError);
  });
});

describe("formatPrice", () => {
  it("groups thousands, keeps the decimals and names the currency", () => {
    expect(formatPrice("1234.5000", "USD")).toBe(`1.234,5${NBSP}USD`);
    expect(formatPrice("0", "VND")).toBe(`0${NBSP}VND`);
    expect(formatPrice("18450000000.00", null)).toBe("18.450.000.000");
    expect(formatPrice("0.0001", "USD")).toBe(`0,0001${NBSP}USD`);
  });
});
