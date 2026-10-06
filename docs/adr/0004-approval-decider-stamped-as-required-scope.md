---
status: Accepted
date: 2026-10-06
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide, may_decide
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/adapters/langgraph_runner.py # _create_approval
    - ../../packages/python/dw_platform/src/dw_platform/application/authorization.py # holds_stamped_scope
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
- `/approvals` still lists requests the viewer cannot decide.
- Approval RLS stays tenant-only; reads are narrowed to the caller's workspace
  in the repository (`platform-runtime/approval-audit-and-workspace` 02).
