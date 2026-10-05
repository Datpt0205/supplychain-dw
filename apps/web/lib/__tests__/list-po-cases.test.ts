import { describe, expect, it } from "vitest";
import { ApiClient } from "@dw/api-client";

/**
 * The query string `listPOCases` sends is a contract with the route's own
 * parameter names. A misspelled one is not an error anywhere — the server
 * ignores an unknown parameter and answers with the UNFILTERED list, so a
 * Control Tower drill-down for one supplier would quietly show every case.
 * The server-side tests cannot see that; only the client's own output can.
 */
function recordingClient(): { client: ApiClient; sent: URL[] } {
  const sent: URL[] = [];
  const fetchImpl: typeof fetch = async (input) => {
    sent.push(new URL(String(input)));
    return new Response(JSON.stringify({ items: [], next_cursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  return {
    client: new ApiClient({ baseUrl: "http://api.test", fetchImpl }),
    sent,
  };
}

/** The one request a call made — fails loudly if there was none. */
function onlyRequest(sent: URL[]): URL {
  const [url, ...rest] = sent;
  if (!url || rest.length > 0) {
    throw new Error(`expected exactly one request, got ${sent.length}`);
  }
  return url;
}

describe("ApiClient.listPOCases", () => {
  it("sends each filter under the name the route reads", async () => {
    const { client, sent } = recordingClient();

    await client.listPOCases({
      limit: 10,
      cursor: "abc",
      state: "qc",
      supplierName: "Elmich Co.",
      activeOnly: true,
    });

    expect(onlyRequest(sent).pathname).toBe("/api/v1/supply-chain/po-cases");
    expect(Object.fromEntries(onlyRequest(sent).searchParams)).toEqual({
      limit: "10",
      cursor: "abc",
      state: "qc",
      supplier_name: "Elmich Co.",
      active_only: "true",
    });
  });

  it("sends nothing for a filter left unset", async () => {
    const { client, sent } = recordingClient();

    await client.listPOCases({ activeOnly: false });

    expect(onlyRequest(sent).search).toBe("");
  });

  it("keeps a free-text supplier name as one exact value", async () => {
    const { client, sent } = recordingClient();
    const name = "Quiet & Sons + Co #2 — Nhà Máy Đồng Nai";

    await client.listPOCases({ supplierName: name });

    expect(onlyRequest(sent).searchParams.getAll("supplier_name")).toEqual([
      name,
    ]);
    // Nothing leaked out of the value into a parameter of its own.
    expect([...onlyRequest(sent).searchParams.keys()]).toEqual([
      "supplier_name",
    ]);
  });
});
