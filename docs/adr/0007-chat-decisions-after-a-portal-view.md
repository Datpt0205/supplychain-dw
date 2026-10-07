---
status: Accepted
date: 2026-10-07
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide(channel=, admission=)
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_codes.py # ApprovalViewService
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/channel_decisions.py # grammar, ChannelApprovalDecisionService, ChannelDecisionCommand
    - ../../packages/python/dw_platform/src/dw_platform/application/approval_codes.py # DecisionCodeKey, ApprovalSubjectVersions
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/approval_codes.py # SqlApprovalCodeStore
    - ../../db/migrations/versions/e399be8c0a2d_platform_approval_view_receipts_and_.py
    - ../../apps/web/app/approvals/[id]/page.tsx # "Lấy mã"
---

# 0007. An approval may be decided in a chat, but only after viewing the current version on the portal, with a single-use code

Accepted in the first product built on the platform (its ADR 0014, 2026-10-05,
amended 2026-10-06 and 2026-10-07), and upstreamed from there; the product's
subject-version ports stay in the product.

## Context

A chat bot has no buttons, and a chat id carries no tenant. Deciding by free
text would be a second decision path with none of the web's guarantees: that
the decider saw what they decide, holds the stamped scope (ADR 0004), and is
not the requester.

## Decision

1. **Opening an approval on the portal records a view receipt**
   (`POST /api/v1/approvals/{id}/view`): who, which approval, the approval's
   `version` and the subject's version at that moment.
2. **With `issue_code`, the view issues a single-use code**: six digits, ten
   minutes, bound to the receipt and to the comment typed on the page, stored
   only as HMAC-SHA256 under `DW_APPROVAL_CODE_SECRET` (a plain hash of six
   digits is reversed by a million tries). Issuing a new code revokes the open
   one. No secret, no code: approvals are then decided on the web only.
3. **The code never travels in a chat message.** It is shown only on the page,
   after sign-in. Notifications carry a title and a link.
4. **Fixed grammar, no model:** `DUYỆT <6 digits>` exactly, or
   `KHÔNG <6 digits> <reason>`. The decide command is registered first in the
   `ChannelCommandRegistry`, so a decision is never read as anything else or
   shown to a model.
5. **A decision from a chat is accepted only if all hold:** the chat's link
   names the person the code was issued to; the code is for that approval,
   unused, unrevoked and unexpired; the approval and subject versions on the
   receipt still match; the person holds `approvals.decide` and the stamp,
   recomputed now from their current membership, with a context cut to exactly
   that; and they are not the requester, for every approval type.
6. **One decision path.** The chat decision goes through
   `ApproveAndResumeService.decide(channel="zalo", admission=CodeAdmission)`:
   every web check runs once, in one place; the admission consumes the code in
   the decision's transaction, after every check and before any write, so a
   refused decision leaves the code usable. `platform.approval_decisions`
   records `channel`.
7. **Five wrong tries lock the code;** every wrong try counts against each of
   the sender's open codes (the message names no approval). Any code that
   matches nothing gets one sentence, so it reveals no one's approval; each
   refusal is audited under the code's tenant.
8. **The subject's version is the owning context's answer**, through
   `ApprovalSubjectVersionPort`, registered by `approval_type` prefix at the
   composition root (`ApprovalSubjectVersions`). A type no context answers for
   gets no code: decided on the web only (closed when missing). The platform
   registers none; a context registers its own in `wiring.py` and, in the
   worker, passes its registry to `build_channel_decision_command`.
9. **The worker decides on the runner that hosts the approval's graph,** so a
   decided run resumes from its checkpoint there. The platform worker hosts no
   graph and so registers no decide command itself; a context that hosts its
   graph in the worker builds one over its runner.

## Threats

| Case                                          | What stops it                                                                                                         |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| A forwarded message                           | It carries no code.                                                                                                   |
| A code copied to another chat                 | The chat's link names someone else: refused, and the wrong try counts against the sender.                             |
| Replay, or Zalo delivering an update twice    | The code is consumed once in the decision's transaction; an approval is decided once; the message id is deduplicated. |
| The subject changed after the view            | The versions on the receipt differ: refused, with "open it again for a new code".                                     |
| The requester approving their own request     | No code for the requester; the chat path refuses the requester for every type; `decide` refuses for strict prefixes.  |
| Free text or prompt injection ("approve all") | Not the grammar, so not a decision; no model is on the path.                                                          |
| Losing the scope after the view               | Authority is recomputed at decision time.                                                                             |

## Alternatives considered

- **Decide on the web only.** Approvers are often away from a desk.
- **Text decisions without a prior view.** Proves nothing about what was seen.
- **The code inside the chat message.** Proves only that someone holds the
  phone.
- **A model reading the reply into a decision.** Asked to choose, a model will
  choose even when the message said nothing.

## Consequences

- Two RLS tables, `approval_view_receipts` and `approval_decision_codes`; codes
  are pruned after a day by `approval_codes_retention`.
- The portal page (`/approvals/[id]`) must work at 320 px: the approver opens it
  on a phone to get the code.
