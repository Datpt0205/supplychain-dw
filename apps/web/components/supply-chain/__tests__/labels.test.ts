import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { caseStateSchema, productDevStateSchema } from "@dw/contracts";
import {
  CASE_STATE_LABEL,
  CASE_STATE_META,
  stepLabel,
} from "../case-state-badge";
import {
  PRODUCT_DEV_STATE_LABEL,
  PRODUCT_DEV_STATE_META,
} from "../product-case-labels";

/**
 * The label tables are the code's one owner of each state's words, and
 * CONTEXT.md copies them for reading ("when the two disagree, fix both in the
 * same commit"). This makes them disagree loudly instead of quietly
 * (failure-modes #2): each state the code knows reads the same in both.
 */
const CONTEXT = readFileSync(
  resolve(
    fileURLToPath(import.meta.url),
    "../../../../../../packages/python/dw_supply_chain/CONTEXT.md",
  ),
  "utf8",
);

/** The `| \`value\` | Nhãn | … |` rows under one heading of CONTEXT.md. */
function glossaryTable(heading: string): Map<string, string> {
  const start = CONTEXT.indexOf(heading);
  expect(start).toBeGreaterThan(-1);
  const section = CONTEXT.slice(start).split("\n### ")[0]!.split("\n## ")[0]!;
  const rows = new Map<string, string>();
  for (const line of section.split("\n")) {
    const match = /^\|\s*`([a-z_]+)`\s*\|\s*([^|]+?)\s*\|/.exec(line);
    if (match) rows.set(match[1]!, match[2]!);
  }
  return rows;
}

describe("PO case states (CASE_STATE_LABEL)", () => {
  const glossary = glossaryTable("### Trạng thái của Hồ sơ PO");

  it.each(caseStateSchema.options)(
    "%s reads as CONTEXT.md writes it",
    (state) => {
      expect(CASE_STATE_LABEL[state]).toBe(glossary.get(state));
    },
  );

  it("gives every in-progress state a step of stage 2 (10–17)", () => {
    for (const state of caseStateSchema.options) {
      const { step } = CASE_STATE_META[state];
      if (step !== null) {
        expect(step).toBeGreaterThanOrEqual(10);
        expect(step).toBeLessThanOrEqual(17);
      }
    }
    expect(stepLabel(CASE_STATE_META.waiting_deposit.step)).toBe(
      "Bước 11 · Giai đoạn 2",
    );
  });

  it("never colours an interruption as progress", () => {
    for (const state of ["blocked", "manual_review", "rework"] as const) {
      expect(CASE_STATE_META[state].tone).not.toBe("pri");
      expect(CASE_STATE_META[state].tone).not.toBe("ok");
    }
  });
});

describe("product development states (PRODUCT_DEV_STATE_LABEL)", () => {
  const glossary = glossaryTable(
    "### Trạng thái của Hồ sơ phát triển sản phẩm",
  );

  it.each(productDevStateSchema.options)(
    "%s reads as CONTEXT.md writes it",
    (state) => {
      expect(PRODUCT_DEV_STATE_LABEL[state]).toBe(glossary.get(state));
    },
  );

  it("puts every in-progress state in stage 1 (1–9)", () => {
    for (const state of productDevStateSchema.options) {
      const { step } = PRODUCT_DEV_STATE_META[state];
      if (step !== null) expect(step).toBeLessThanOrEqual(9);
    }
    expect(stepLabel(PRODUCT_DEV_STATE_META.pending_bod_review.step)).toBe(
      "Bước 6 · Giai đoạn 1",
    );
  });
});
