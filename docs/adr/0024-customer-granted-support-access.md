---
status: Proposed (provisional, decided by the agent on Đạt's delegation 2026-10-08)
date: 2026-10-08
source:
    - ../../db/migrations/versions/af8ee878b4ab_platform_support_staff_and_support_.py
    - ../../packages/python/dw_platform/src/dw_platform/application/support_access.py
    - ../../.claude/plans/platform-runtime/support-access/spec.md
---

# 0024. Customer-granted support access: scoped, time-limited, revocable, on the customer's record

Numbered 0024 so that it collides with no ADR on `main` or on a product branch
that merges from it (those use numbers up to 0023).

## Context

Once real customers run on the platform, the operators will need to look at a
customer's data: a document read wrongly, a rule misfiring, data entry the
customer asked for during onboarding. Before this decision there were two ways
in, and both were wrong:

- **Add yourself as `platform_admin`.** `ScopeAuthorizationService.is_allowed`
  passes every scope check for that role. No limit in time, no limit in scope,
  and the customer does not know.
- **Ask for the customer's password.** Acting under someone else's identity:
  the audit trail names the wrong person, and the platform's rule that only a
  verified identity is trusted is broken by design.

The platform operator's own context (`ProvisioningContext`) is deliberately
tenant-less and cannot read business data, so it is not a third way.

## Decision

1. **The customer asks, the operators choose the person.** A member holding
   `support.request` (given to `org_admin`) files a request; a member holding
   `support.grant` grants it directly or approves a request. The customer picks
   a scope set, a resource (the workspace, or one resource a context names),
   a duration (1 to 336 hours) and a reason. The customer never names the staff
   member: the operators assign one from `platform.support_staff`. A refusal on
   conflict-of-interest grounds can then never reveal to one customer that the
   operators also serve a competitor.
2. **A granter only hands over scopes they hold.** Granting and approving check
   that every scope of the set is among the granter's own scopes in that
   workspace; the scopes are stamped on the grant. `support.*` is read
   literally from the caller's scopes: no role stands in for it,
   `platform_admin` included.
3. **A grant is in force only while all of these hold, checked on every
   request:** it was assigned and is not past `expires_at`; it was not revoked;
   the granter still holds `support.grant` and every stamped scope in that
   workspace. `grant_effective_state` is the one reading of this rule, used by
   the customer's list and by the support access context, so the screen and the
   door cannot disagree. "Expired" and "ineffective" are derived, never stored.
4. **The staff member uses their own identity, and the support context carries
   no role.** Tenant and workspace come from the grant, never from a client
   header; `roles` is empty; `scopes` is exactly the stamped set. It never
   merges with any membership the staff member holds, `platform_admin`
   included. It is never cached (the access-context cache fails open). A second
   factor is required.
5. **Support staff are never members of a customer tenant.** A trigger on
   `platform.memberships` refuses to insert a membership for a listed staff
   member, or to change one's roles, whichever path writes it (org admin,
   invitation, the operator's own org-admin assignment). Otherwise an org admin
   could invite the staff member with a business role, unlimited in time.
6. **What support may never carry.** A scope set registered by a context is
   refused if it carries a scope beginning with `approvals.`, `runs.`,
   `audit.`, `knowledge.`, `memory.`, `platform.`, `support.` or `directory.`.
   Support does not decide, does not read runs, audit or knowledge, does not
   administer. A missing scope only stops a route that checks one, so routes
   refuse a support context by default and a context opens one route at a time,
   with a negative test, through `SUPPORT_ALLOWED_ROUTES`.
7. **The customer sees and stops it.** Every step of a grant, and every request
   made under one, writes an event to the tenant's own audit log (identifiers
   only, never the reason). Revocation takes effect at the next request.
8. **Off by default.** The `support_access` feature flag is in no plan; a
   tenant gets it through `entitlements.feature_overrides`. Without it the
   customer's routes refuse, and an existing grant builds no context.
9. **The platform does not know a context's resources.** A context registers
   its grantable scope sets and a `SupportResourcePort` that names its resources
   at the composition root; narrowing reads to one resource is that context's
   business. The platform names `workspace` itself.

### Provisional calls (Đạt delegated the open calls on 2026-10-08)

- **Refusal shape.** A support refusal keeps the platform's status and `code`
  (`permission_denied` 403, `conflict` 409, `validation_failed` 422) and says
  which refusal in `details.reason_code` (`support_access_not_enabled`,
  `support_scope_not_held`, `support_scope_set_unknown`,
  `support_grant_wrong_status`, `support_staff_required`,
  `support_staff_not_member`, and the support context's own codes). The public
  `ErrorCode` taxonomy, mirrored in TypeScript, does not grow by one code per
  feature.
- **Who may close.** Rejecting and revoking need `support.grant` but not the
  feature flag: switching support off must never leave a customer unable to
  close an access.
- **The staff member's foreign key** is `ON DELETE RESTRICT`: a grant always
  names who held it. The other actor columns are `ON DELETE SET NULL`, so the
  "present if and only if" CHECKs sit on each step's timestamp.
- **The status machine is the database's** (`platform.guard_support_grant`):
  insert only as `pending_approval` or `pending_assignment`; approve, reject,
  assign, revoke move forward only; what a step decided never changes. The
  per-tenant code (`SG-0001`) is assigned there too.

## Alternatives considered

- **`platform_admin` for support.** Rejected: unlimited scope and time,
  invisible to the customer.
- **The customer picks a named staff member.** Rejected: a conflict-of-interest
  refusal tells the customer something about another customer, and trying
  names one by one probes it.
- **The customer's password or one-time code.** Rejected: someone else's
  identity, an audit trail naming the wrong person.
- **Screen sharing only.** Still usable as the light option, but cannot
  investigate while the customer is offline.

## Consequences

- New tables `platform.support_staff` (identity plane, provisioner-written) and
  `platform.support_grants` (tenant RLS, forced; the customer's side writes only
  its own steps' columns, the provisioner only the assignment's).
- The provisioner may append to `platform.audit_events`, so the customer's
  trail records who assigned whom in the same transaction.
- `org_admin` gains `support.request`. `support.grant` is given by a context to
  the role that owns the data.

## Open

1. **Conflict of interest** when assigning a person. Not checked; until a way to
   check it is decided, only tenants with the flag can be assigned at all.
2. **Email linking.** Staff sign in through the operators' realm; the
   email-based linking in `identity_provisioning.py` must be closed (or require
   an email verified by the tenant's own IdP) before customer SSO is brokered.
3. **Where the data plane runs at the customer.** How a staff identity reaches a
   data plane hosted by the customer is not decided here.
