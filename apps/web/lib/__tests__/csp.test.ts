import { describe, expect, it, vi } from "vitest";

vi.mock("../auth/config", () => ({
  API_BASE_URL: "https://api.example.vn/base",
  KEYCLOAK_URL: "https://auth.example.vn",
}));

import { contentSecurityPolicy } from "../csp";

function directives(policy: string): Map<string, string[]> {
  return new Map(
    policy.split(";").map((part) => {
      const [name, ...values] = part.trim().split(/\s+/);
      return [name!, values];
    }),
  );
}

describe("the page's Content-Security-Policy", () => {
  const prod = directives(contentSecurityPolicy({ nonce: "abc", dev: false }));

  it("runs only scripts carrying this request's nonce", () => {
    const script = prod.get("script-src")!;
    expect(script).toContain("'nonce-abc'");
    expect(script).toContain("'strict-dynamic'");
    expect(script).not.toContain("'unsafe-inline'");
    expect(script).not.toContain("'unsafe-eval'");
    expect(script).not.toContain("*");
  });

  it("allows eval only under next dev", () => {
    const dev = directives(contentSecurityPolicy({ nonce: "abc", dev: true }));
    expect(dev.get("script-src")).toContain("'unsafe-eval'");
  });

  it("connects only to this origin, the API and the identity provider", () => {
    expect(prod.get("connect-src")).toEqual([
      "'self'",
      "https://api.example.vn",
      "https://auth.example.vn",
    ]);
  });

  it("is never framed and loads no plugin", () => {
    expect(prod.get("frame-ancestors")).toEqual(["'none'"]);
    expect(prod.get("object-src")).toEqual(["'none'"]);
    expect(prod.get("base-uri")).toEqual(["'self'"]);
  });
});
