"use client";

import { useCallback } from "react";
import type { ProductCategory } from "@dw/contracts";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";

/**
 * The tenant's Category list (ADR 0019), read from the server, never kept
 * here: the propose form offers it, and a case's stamped key is shown by its
 * label. The server refuses a key the list does not have.
 */
export function useProductCategories() {
  return useCachedResource(
    "supply-chain:product-categories",
    useCallback(() => apiClient().listProductCategories(), []),
  );
}

/** A case's Category in words: the label of its key, or, for a case opened
 * before the list existed (or a key since removed), the stamp as it is. */
export function categoryLabel(
  categories: readonly ProductCategory[] | null,
  key: string,
): string {
  return categories?.find((category) => category.key === key)?.label ?? key;
}
