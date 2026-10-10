import { hasAnyRole } from "./roles";
import type { NavContext, NavItem } from "./types";

/** What the menu knows about the person looking at it. */
export interface NavViewer {
  isPlatformOperator: boolean;
  hasScope: (scope: string) => boolean;
  roles: readonly string[];
}

/**
 * Whether a nav item is offered to this viewer: the ONE rule, read by the menu
 * (`components/app-frame.tsx`) and the home page's shortcuts alike, so the
 * home page never invites someone to an item the menu hides.
 *
 * Navigation, not authorization: the API checks the scope on every request.
 */
export function isNavItemVisible(item: NavItem, viewer: NavViewer): boolean {
  if (item.operatorOnly && !viewer.isPlatformOperator) return false;
  if (item.scope && !viewer.hasScope(item.scope)) return false;
  if (item.anyScope !== undefined) {
    // An empty list asks for nothing and would show to everyone: refuse it.
    if (item.anyScope.length === 0) return false;
    if (!item.anyScope.some((scope) => viewer.hasScope(scope))) return false;
  }
  if (item.roles && !hasAnyRole(viewer.roles, item.roles)) return false;
  return true;
}

/** The pages a person can reach, by `isNavItemVisible`. */
export function visibleNav(
  items: readonly NavItem[],
  viewer: NavViewer,
): NavItem[] {
  return items.filter((item) => isNavItemVisible(item, viewer));
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
