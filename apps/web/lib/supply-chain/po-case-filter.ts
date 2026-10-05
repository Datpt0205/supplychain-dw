import { caseStateSchema, type POCaseListFilter } from "@dw/contracts";

/**
 * The PO case list's filter as it lives in a URL — the one place its query
 * parameter names are spelled. The list page reads and writes it and the
 * Control Tower links into it; both go through here, so a drill-down link
 * cannot drift from what the list reads. The names are the API's own
 * (`state`, `supplier_name`, `active_only`), so the page URL and the request
 * it makes read the same.
 */
export type ListFilter = POCaseListFilter & { activeOnly: boolean };

export const PO_CASES_PATH = "/supply-chain/po-cases";

/**
 * The filter a URL describes. `state` is checked against the closed set the
 * server accepts, so a stale or hand-edited value is treated as absent
 * rather than turned into an error page for a link that merely aged. A
 * `supplier_name` is free text and is passed through as written — a
 * malformed one surfaces as the server's own refusal, not a guess.
 */
export function readPOCaseFilter(
  params: Pick<URLSearchParams, "get">,
): ListFilter {
  const state = caseStateSchema.safeParse(params.get("state"));
  const supplierName = params.get("supplier_name");
  return {
    state: state.success ? state.data : undefined,
    supplierName: supplierName || undefined,
    activeOnly: params.get("active_only") === "true",
  };
}

/** The list page's URL for `filter`, unset fields left out. */
export function poCasesHref(filter: POCaseListFilter): string {
  const params = new URLSearchParams();
  if (filter.state) params.set("state", filter.state);
  if (filter.supplierName) params.set("supplier_name", filter.supplierName);
  if (filter.activeOnly) params.set("active_only", "true");
  const query = params.toString();
  return query ? `${PO_CASES_PATH}?${query}` : PO_CASES_PATH;
}
