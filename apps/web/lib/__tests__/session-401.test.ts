import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The keycloak adapter is not exercised here; keep it from touching the DOM.
vi.mock("../auth/keycloak", () => ({ getKeycloak: () => ({}) }));

/**
 * A missing or unverifiable bearer token is 401 since the platform split it
 * from 403 (platform-runtime/unauthenticated-401). The client bounces to the
 * login screen on a 401 - but only for a request that carried a token, i.e. a
 * session that died. A request sent without one (the login screen's own calls)
 * answering 401 must not bounce, or the login page reloads itself forever.
 */

let assigned: string[] = [];

beforeEach(() => {
  vi.resetModules();
  assigned = [];
  const location = {
    get href() {
      return "http://localhost/somewhere";
    },
    set href(value: string) {
      assigned.push(value);
    },
  };
  Object.defineProperty(window, "location", {
    value: location,
    configurable: true,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { status: 401 })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function fetchImpl() {
  const { clientOptions } = await import("../session");
  const impl = clientOptions("http://api.test").fetchImpl;
  if (!impl) throw new Error("clientOptions must supply a fetchImpl");
  return impl;
}

describe("the 401 bounce", () => {
  it("sends a session whose token died back to sign in, once per burst", async () => {
    const impl = await fetchImpl();
    const withToken = { headers: { Authorization: "Bearer expired" } };

    await Promise.all([
      impl("http://api.test/a", withToken),
      impl("http://api.test/b", withToken),
      impl("http://api.test/c", withToken),
    ]);

    expect(assigned).toEqual(["/"]);
  });

  it("does not bounce a request that carried no token", async () => {
    const impl = await fetchImpl();

    const response = await impl("http://api.test/auth/bootstrap", {});

    expect(response.status).toBe(401);
    expect(assigned).toEqual([]);
  });

  it("leaves a 403 to the caller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 403 })),
    );
    const impl = await fetchImpl();

    await impl("http://api.test/a", {
      headers: { Authorization: "Bearer ok" },
    });

    expect(assigned).toEqual([]);
  });
});
