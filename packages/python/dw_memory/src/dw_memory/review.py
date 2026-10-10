"""A memory the policy will not write alone goes to a person, through the
platform's approval inbox.

`MemoryService.propose` opens one `memory.review` approval per REVIEW candidate,
in the candidate's own transaction. Deciding it is the platform's ordinary
`decide`; the decision reaches memory as the outbox event the approval flow
writes for every run-less approval, and `MemoryService.settle_review` acts on
it in the worker. This module holds what both sides name: the approval type and
the one condition memory puts on who may decide it.
"""

from __future__ import annotations

from dw_kernel.errors import PermissionDeniedError
from dw_knowledge.contracts import classifications_for_clearance
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.approval import ApprovalRequest, decided_event_type

__all__ = ["MEMORY_REVIEW", "MEMORY_REVIEW_DECIDED", "require_clearance_for_review"]

MEMORY_REVIEW = "memory.review"
"""The approval type of a held memory candidate."""

MEMORY_REVIEW_DECIDED = decided_event_type(MEMORY_REVIEW)
"""What the approval flow announces when one is decided."""


def require_clearance_for_review(request: ApprovalRequest, context: AccessContext) -> None:
    """A person may not decide on a memory they are not cleared to read.

    Approving a fact is vouching for its content, and rejecting it is judging
    that content; neither is possible without reading it, and the candidate's
    content is served only to a clearance that covers it. The label is the one
    stamped on the approval when it was opened, written by memory and not by the
    client, and read through the same ladder recall filters by.

    A payload without a label refuses: it is not a memory review this build
    opened, and "no label" must not read as "nothing to protect".
    """
    classification = request.payload.get("classification")
    if not isinstance(classification, str) or classification not in (
        classifications_for_clearance(context.clearance)
    ):
        raise PermissionDeniedError(
            "your clearance does not cover the memory this approval would decide",
            details={"approval_id": str(request.id), "clearance": context.clearance},
        )
