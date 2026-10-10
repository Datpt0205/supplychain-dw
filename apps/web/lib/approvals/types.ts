import type { ApiClient } from "@dw/api-client";
import type { Approval } from "@dw/contracts";

/**
 * A context that owns some approvals: by prefix of their `approval_type`, the
 * service that decides them and, when it has one, its own inbox.
 */
export interface ApprovalHost {
  /** Approval types starting with this prefix belong to this host. */
  prefix: string;
  /** The service that decides them; the platform API when omitted. */
  client?: () => ApiClient;
  /**
   * The context's own inbox for these approvals. `/approvals` then links
   * there instead of offering a comment and the two decisions. Navigation,
   * not authorization: the platform route still accepts a decision from a
   * holder of `approvals.decide`, and a context that must refuse one checks
   * where its run resumes. `href` must be an in-app path ("/…").
   */
  inbox?: { label: string; href: (approval: Approval) => string };
}

/** Where a pending approval is decided, when it is not on `/approvals`. */
export type ApprovalInbox =
  | { kind: "link"; label: string; href: string }
  /** The host's `href` is not an in-app path: no link and no decision. */
  | { kind: "misconfigured" };
