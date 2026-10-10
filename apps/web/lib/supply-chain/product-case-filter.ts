import { productDevStateSchema, type ProductDevState } from "@dw/contracts";

/**
 * The product case list's filter as it lives in a URL — the one place its
 * query parameter names are spelled. The list page reads and writes it; the
 * command bar and the daily brief link into it (stage-1 ticket 08), and the
 * Zalo reply builds the same URL server-side (`zalo_case_query.
 * _product_list_url`), so a "see all" link narrows exactly as the answer did.
 *
 * `mine` is the page's own toggle ("Tôi là PIC"); `pic` names a person by id,
 * what an answer resolved a name (or "của tôi") to.
 */
export interface ProductListFilter {
  state?: ProductDevState;
  mine: boolean;
  pic?: string;
  category?: string;
}

export const PRODUCT_CASES_PATH = "/supply-chain/product-cases";

/** The filter a URL describes; a stale or hand-edited state reads as absent. */
export function readProductCaseFilter(
  params: Pick<URLSearchParams, "get">,
): ProductListFilter {
  return {
    state: productDevStateSchema.safeParse(params.get("state")).data,
    mine: params.get("mine") === "1",
    pic: params.get("pic") || undefined,
    category: params.get("category") || undefined,
  };
}

/** The list page's URL for `filter`, unset fields left out. */
export function productCasesHref(filter: Partial<ProductListFilter>): string {
  const params = new URLSearchParams();
  if (filter.state) params.set("state", filter.state);
  if (filter.mine) params.set("mine", "1");
  if (filter.pic) params.set("pic", filter.pic);
  if (filter.category) params.set("category", filter.category);
  const query = params.toString();
  return query ? `${PRODUCT_CASES_PATH}?${query}` : PRODUCT_CASES_PATH;
}
