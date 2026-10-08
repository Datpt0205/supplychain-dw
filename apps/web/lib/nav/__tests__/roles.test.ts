import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { OPERATOR_LABEL, UNNAMED_ROLE, roleLabel, roleLabels } from "../roles";

/** The role keys the platform's reference data seeds (`platform.roles`). */
function seededRoleKeys(): string[] {
  const sql = readFileSync(
    resolve(
      __dirname,
      "../../../../../db/migrations/sql/0001_platform_reference.sql",
    ),
    "utf8",
  );
  const start = sql.indexOf("INSERT INTO platform.roles");
  expect(start).toBeGreaterThanOrEqual(0);
  const block = sql.slice(start, sql.indexOf(";", start));
  return [...block.matchAll(/\(\s*'([a-z_]+)'\s*,/g)].map((m) => m[1]!);
}

/** The role keys a bounded context seeds in its own migrations: Supply
 * Chain's, by the "Supply Chain — …" catalog name each is created with. */
function contextRoleKeys(): string[] {
  const dir = resolve(__dirname, "../../../../../db/migrations/versions");
  const keys = new Set<string>();
  for (const name of readdirSync(dir).filter((n) => n.endsWith(".py"))) {
    const text = readFileSync(resolve(dir, name), "utf8");
    for (const m of text.matchAll(
      /["'](sc_[a-z_]+)["'](?:\s*:\s*\(|,)\s*["']Supply Chain/g,
    )) {
      keys.add(m[1]!);
    }
  }
  return [...keys];
}

describe("role labels", () => {
  it("names every role the platform seeds, in Vietnamese, never by its key", () => {
    const keys = seededRoleKeys();
    expect(keys).toContain("platform_admin");
    for (const key of keys) {
      const label = roleLabel(key);
      expect(label, key).not.toBe(UNNAMED_ROLE);
      expect(label, key).not.toBe(key);
      expect(label, key).not.toMatch(/_|admin/i);
    }
  });

  it("names every role a bounded context seeds, in Vietnamese, never by its key", () => {
    const keys = contextRoleKeys();
    expect(keys).toContain("sc_operator");
    expect(keys).toContain("sc_bod");
    for (const key of keys) {
      const label = roleLabel(key);
      expect(label, key).not.toBe(UNNAMED_ROLE);
      expect(label, key).not.toMatch(/_|admin|Supply Chain/i);
    }
  });

  it("falls back to the catalog's name, then to a generic label, never the code", () => {
    expect(roleLabel("xx_unlisted", "Vai riêng của khách")).toBe(
      "Vai riêng của khách",
    );
    expect(roleLabel("xx_unlisted")).toBe(UNNAMED_ROLE);
    expect(roleLabels(["member", "xx_unlisted"])).toBe(
      `Nhân viên, ${UNNAMED_ROLE}`,
    );
  });

  it("does not name the operator after a role", () => {
    expect(OPERATOR_LABEL).not.toBe(roleLabel("platform_admin"));
  });
});
