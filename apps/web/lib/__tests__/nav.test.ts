import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "../nav/registry";
import { barNav, visibleNav, type NavViewer } from "../nav/visibility";

function viewer(scopes: string[], roles: string[] = []): NavViewer {
  return {
    isPlatformOperator: false,
    hasScope: (scope) => scopes.includes(scope),
    roles,
  };
}

const SC_READ = ["supply_chain.po_case.read", "supply_chain.product_case.read"];

describe("barNav: the navbar a person gets", () => {
  it("is Supply Chain's pages, and the approvals inbox, for someone whose work is Supply Chain", () => {
    const { items, context } = barNav(
      visibleNav(
        NAV_ITEMS,
        viewer([...SC_READ, "approvals.read", "knowledge.read", "memory.read"]),
      ),
    );
    expect(context?.product).toBe("Supply Chain");
    expect(items.map((item) => item.href)).toEqual([
      "/supply-chain/daily-brief",
      "/supply-chain/follow-ups",
      "/supply-chain/product-cases",
      "/supply-chain/po-cases",
      "/supply-chain/attention-queue",
      "/supply-chain/control-tower",
      "/approvals",
    ]);
  });

  it("keeps every page for someone who administers the tenant", () => {
    const { items, context } = barNav(
      visibleNav(
        NAV_ITEMS,
        viewer([...SC_READ, "approvals.read", "platform.members.read"]),
      ),
    );
    expect(context).toBeNull();
    expect(items.map((item) => item.href)).toEqual(
      expect.arrayContaining(["/", "/admin", "/supply-chain/po-cases"]),
    );
  });

  it("keeps the platform's nav for someone who reaches no context", () => {
    const { items, context } = barNav(
      visibleNav(NAV_ITEMS, viewer(["approvals.read", "knowledge.read"])),
    );
    expect(context).toBeNull();
    expect(items.map((item) => item.href)).toEqual([
      "/",
      "/approvals",
      "/knowledge",
    ]);
  });

  it("leaves a page off the bar only, never out of what visibleNav allows", () => {
    // A page the person lacks the scope for is not offered in either view.
    const { items } = barNav(visibleNav(NAV_ITEMS, viewer(SC_READ)));
    expect(items.map((item) => item.href)).not.toContain("/approvals");
  });
});
