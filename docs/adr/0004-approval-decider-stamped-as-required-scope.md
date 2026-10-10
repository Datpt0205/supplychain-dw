---
status: Accepted
date: 2026-10-06
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide, may_decide
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/adapters/langgraph_runner.py # _create_approval
    - ../../packages/python/dw_platform/src/dw_platform/application/authorization.py # holds_stamped_scope, ApprovalAudience
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/repositories.py # visible_to
    - ../../db/migrations/versions/36dabf47619c_platform_approval_requests_required_.py
---

# 0004. Who may decide an approval is stamped on it as `required_scope`

Numbered 0004 because code in this repo cites `ADR-001`..`ADR-003`, documents
that stayed with the product this platform was extracted from
(`docs/agents/domain.md`); a new 0001 would read as the target of those.

Accepted in the first product built on the platform (its ADR 0020, 2026-10-05;
the `platform_admin` rule 2026-10-06, decided under a delegation from Đạt), and
upstreamed from there unchanged.

## Context

`ApproveAndResumeService.decide` asked only for `approvals.decide`, so anyone
who could approve anything in a workspace could approve every request in it.
A strict prefix stops self-approval and demands a comment; it says nothing
about _who_. A business gate ("only the board approves this step") had no
place to live.

## Decision

- `platform.approval_requests.required_scope text NULL`, with a CHECK on the
  shape of a scope name (`ck_approval_requests_required_scope`, the one owner
  of that shape). `ApprovalRequest.required_scope` mirrors it. NULL keeps the
  old rule, so existing requests are unchanged.
- **Stamped when raised.** `_create_approval` reads `required_scope` from the
  node's interrupt payload, untouched. The node writes it from code and the
  tenant's policy; a tool call's own arguments sit nested under `payload`, so
  no model can set it. A malformed value fails the INSERT and the run ends
  `failed` with no approval row.
- **Written once.** `dw_app` keeps only a column UPDATE grant on `status`,
  `decided_at`, `version`; nothing can move a stamp after the fact.
- **Enforced where the decision is written.** Approving, or rejecting someone
  else's request, needs `approvals.decide` and then the stamp, before any
  write or resume. Withdrawing your own request needs neither.
- **No role stands in for the stamp, `platform_admin` included.**
  `holds_stamped_scope` is the one owner of that rule; `ScopeAuthorizationService`
  still lets the admin pass every other scope. A platform operator is not the
  business's decider.
- **The read path asks the same functions.** `ApprovalView` carries
  `required_scope`, `can_decide` (`may_decide`: the two checks `decide` runs)
  and `requested_by_me`; `/approvals` locks on `can_decide` and says why in
  words. The page never compares the stamp with the session's `hasScope`,
  which lets an admin pass.

## Considered and rejected

- **Look the policy up at decision time:** a policy changed while a request
  waits would change who may decide it. The stamp is a past decision.
- **Check in the context's node after resume:** the decision is already
  written by then (failure-modes #5).
- **One strict prefix per role:** a prefix says "strict or not", not "who".

## Consequences

- `can_decide` covers scopes only, not separation of duties, the required
  comment or per-type guards; the page handles the first two with
  `requires_comment` and `requested_by_me`.
- `/approvals` still lists unstamped requests the viewer cannot decide, and
  the viewer's own stamped ones; since the amendment below, no other stamped
  request.
- Approval RLS stays tenant-only; reads are narrowed to the caller's workspace
  in the repository (`platform-runtime/approval-audit-and-workspace` 02).

## Amendment 2026-10-07: who may see a stamped request

Decided by the lead under Đạt's delegation ("fail closed"): the platform's
approval model takes the stricter rule from each side. Deciding stays as above.
Seeing follows the rule a second product (Proterial, its ticket 10) shipped:
a request's payload is its decider's working material, not every member's
reading.

- **A stamped request is seen only by who may decide it and by its
  requester.** "May decide" is `approvals.decide` (as `ScopeAuthorizationService`
  answers it, admin rule included) and the stamp (`holds_stamped_scope`, which
  no role passes). Everyone else gets what a request that never existed gets:
  absent from `GET /approvals`, 404 from `GET /approvals/{id}`, 404 from a
  decision (a 403 would confirm it exists and name the scope it needs), and
  not counted by `SqlPendingApprovalQuery`. `platform_admin` without the stamp
  does not see it, consistent with not deciding it.
- **Unstamped requests are unchanged:** every member who may read the inbox
  sees them. The product's stricter default (only `approvals.decide` holders
  see an unstamped request) was not taken; nothing in the decision asked for
  it, and it would hide a member's view of the workspace's pending work.
- **One rule, read and write.** `ApprovalAudience` (in `authorization.py`) owns
  `may_decide` and `may_see`; `ApproveAndResumeService.may_decide` and the
  inbox's `can_decide` ask it. The repository filters in SQL
  (`repositories.visible_to`), required on `get` and `list_pending`, so a page
  is a page of the caller's inbox and never one with holes; the pending query
  reuses the same clause. The decision reads through the same filtered `get`.
  `test_approval_visibility.py` holds the SQL to the Python rule on a real
  database, for every reader, against a table written out by hand.
- **Workers read as the person they act for.** `MemoryService.settle_review`
  reads its approval with the decider's context and no scopes; a
  `memory.review` is never stamped, so it is found.

Considered and rejected: filtering in the route after the read (a page of
the inbox would come back short, and the decision would still answer 403);
hiding stamped requests in the page only (hiding is not authorization).
