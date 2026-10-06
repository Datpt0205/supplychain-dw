import { hasAnyRole } from "./roles";
import type { NavContext, NavItem } from "./types";

/** What the nav filter reads from the signed-in person; `useAuth()` is one. */
export interface NavViewer {
  isPlatformOperator: boolean;
  hasScope: (scope: string) => boolean;
  roles: readonly string[];
}

/**
 * The pages a person can reach. Three filters: scope is the permission the
 * API enforces anyway, role is who the page is for, and operatorOnly is the
 * cross-tenant provisioning area.
 */
export function visibleNav(
  items: readonly NavItem[],
  viewer: NavViewer,
): NavItem[] {
  return items.filter(
    (item) =>
      (!item.operatorOnly || viewer.isPlatformOperator) &&
      (!item.scope || viewer.hasScope(item.scope)) &&
      (!item.roles || hasAnyRole(viewer.roles, item.roles)),
  );
}

/** What the navbar draws: the pages, and the context they belong to. */
export interface Bar {
  items: NavItem[];
  /** The bounded context the bar is, when it is one. */
  context: NavContext | null;
}

/**
 * The bar for what a person can see (`visibleNav`'s output). Someone who
 * reaches exactly one bounded context and administers nothing works in that
 * context: its pages are the bar, with the platform pages its people work in
 * too (`inContextBar`, the approvals inbox where BGĐ decides); the platform's
 * other pages are left off. Anyone who administers the tenant or the platform
 * keeps every page. Navigation, never authorization: a page left off still
 * answers its URL with the server's decision.
 */
export function barNav(visible: readonly NavItem[]): Bar {
  const contexts = new Map<string, NavContext>();
  for (const item of visible) {
    if (item.context) contexts.set(item.context.key, item.context);
  }
  const administers = visible.some((item) => item.administration);
  if (contexts.size === 1 && !administers) {
    const [context] = [...contexts.values()];
    return {
      items: visible.filter(
        (item) => item.context?.key === context!.key || item.inContextBar,
      ),
      context: context!,
    };
  }
  return { items: [...visible], context: null };
}
