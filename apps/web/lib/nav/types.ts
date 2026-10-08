import type { ComponentType } from "react";

/** An `@ant-design/icons` component; drawn `aria-hidden` beside its item's
 * label. */
export type NavIcon = ComponentType<{ "aria-hidden"?: boolean }>;

/** The bounded context a page belongs to, declared by the context's own
 * manifest (see `barNav`). */
export interface NavContext {
  key: string;
  /** What the brand says beside "Digital Worker" in that context's bar. */
  product: string;
}

export interface NavItem {
  href: string;
  label: string;
  hint: string;
  icon: NavIcon;
  exact?: boolean;
  /** Scope required to see this item (omit = always visible). */
  scope?: string;
  /**
   * Shown to a holder of at least one of these scopes, for a page two groups
   * open with different scopes. With `scope` as well, both must hold. An empty
   * list is a configuration error and hides the item. Navigation, not
   * authorization: the API still checks.
   */
  anyScope?: string[];
  /**
   * Roles this item is for; the user needs one of them (omit = every role).
   * This is navigation, not authorization — the API still checks `scope`.
   */
  roles?: string[];
  /** Key into `useNavBadges()` for a count shown beside the label. */
  badgeKey?: string;
  /** Shown only to a Platform Operator (ADR-002), regardless of scope/role. */
  operatorOnly?: boolean;
  /** Set on a bounded context's pages. */
  context?: NavContext;
  /** Administers the tenant or the platform (see `barNav`). */
  administration?: boolean;
  /** A platform page a context's people work in too (the approvals inbox),
   * kept in a context's bar (see `barNav`). */
  inContextBar?: boolean;
}
