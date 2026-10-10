import { describe, expect, it } from "vitest";
import { ApiClient } from "@dw/api-client";

/**
 * The response schema is the whitelist: a `data_view` of a type the client
 * has no component for fails the whole answer rather than reaching the page
 * as data to render some other way.
 */
function clientAnswering(payload: unknown): {
  client: ApiClient;
  sent: Request[];
} {
  const sent: Request[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    sent.push(new Request(String(input), init));
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  return {
    client: new ApiClient({ baseUrl: "http://api.test", fetchImpl }),
    sent,
  };
}

const UNDERSTOOD = {
  state: null,
  supplier_name: null,
  active_only: false,
  po_reference: null,
  product_state: null,
  category: null,
  pic_user_id: null,
  proposal_code: null,
};

describe("ApiClient.askCaseQuery", () => {
  it("posts the question as the route's own body shape", async () => {
    const { client, sent } = clientAnswering({
      intent: "unsupported",
      outcome: "not_understood",
      understood: UNDERSTOOD,
      citations: [],
      ignored_fields: [],
      unusable_fields: [],
      candidates: [],
      data_view: null,
    });

    await client.askCaseQuery("PO nào trễ?");

    const [request] = sent;
    expect(request?.method).toBe("POST");
    expect(new URL(request?.url ?? "").pathname).toBe(
      "/api/v1/supply-chain/case-query",
    );
    expect(await request?.json()).toEqual({ question: "PO nào trễ?" });
  });

  it("refuses an answer whose view type it has no component for", async () => {
    const { client } = clientAnswering({
      intent: "list_cases",
      outcome: "list",
      understood: UNDERSTOOD,
      citations: [],
      ignored_fields: [],
      unusable_fields: [],
      candidates: [],
      data_view: { type: "html", html: "<script>alert(1)</script>" },
    });

    await expect(client.askCaseQuery("PO?")).rejects.toThrow();
  });
});
