import { describe, expect, it, vi } from "vitest";
import type { Approval } from "@dw/contracts";

const platform = { name: "platform" };
vi.mock("../../session", () => ({ apiClient: () => platform }));

import { clientFor, findHost, inboxFor } from "../registry";
import type { ApprovalHost } from "../types";

const own = { name: "own" };

function approval(approval_type: string): Approval {
  return {
    id: "a-1",
    approval_type,
    reason: "r",
    status: "pending",
    run_id: "run-1",
    payload: {},
    created_at: "2026-10-08T00:00:00Z",
    decided_at: null,
    requires_comment: false,
    required_scope: null,
    can_decide: true,
    requested_by_me: false,
  };
}

const HOSTS: ApprovalHost[] = [
  {
    prefix: "ctx.",
    inbox: { label: "Mở hộp duyệt", href: (a) => `/ctx/inbox/${a.id}` },
  },
  { prefix: "svc.", client: () => own as never },
  {
    prefix: "bad.",
    inbox: { label: "x", href: () => "https://evil.example/inbox" },
  },
  { prefix: "proto.", inbox: { label: "x", href: () => "//evil.example/x" } },
];

describe("the approvals registry", () => {
  it("finds the host by prefix, or none", () => {
    expect(findHost(HOSTS, "ctx.order.release")?.prefix).toBe("ctx.");
    expect(findHost(HOSTS, "tool.y")).toBeUndefined();
  });

  it("links a matching approval to its context's inbox", () => {
    expect(inboxFor(HOSTS, approval("ctx.order.release"))).toEqual({
      kind: "link",
      label: "Mở hộp duyệt",
      href: "/ctx/inbox/a-1",
    });
  });

  it("returns null without a match, or for a host with no inbox", () => {
    expect(inboxFor(HOSTS, approval("tool.y"))).toBeNull();
    expect(inboxFor(HOSTS, approval("svc.z"))).toBeNull();
  });

  it("picks the host's client, and the platform's when it has none", () => {
    expect(clientFor(HOSTS, "svc.z")).toBe(own);
    expect(clientFor(HOSTS, "ctx.order.release")).toBe(platform);
    expect(clientFor(HOSTS, "tool.y")).toBe(platform);
  });

  it.each(["bad.x", "proto.x"])(
    "refuses an href outside the app (%s): no link",
    (type) => {
      expect(inboxFor(HOSTS, approval(type))).toEqual({
        kind: "misconfigured",
      });
    },
  );
});
