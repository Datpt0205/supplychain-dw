"""An in-memory `POCaseRepositoryPort` for the reads a case question runs.

Keeps the promise RLS keeps for `SqlPOCaseRepository`: a caller sees only the
cases of its own tenant AND workspace (`tenant_isolation_po_cases`, migration
`62cdcf3bf2d2`), so another tenant's or another workspace's row is simply
absent — never "found but forbidden". Shared by the eval
grader of the chat answer (`eval_graders.grade_chat_case_answer`) and the Zalo
question command's unit tests. The writes and the reads a question never runs
raise `NotImplementedError` rather than pretend.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.ports import PO_REFERENCE_PADDING, POCaseListFilter
from dw_supply_chain.domain.po_case import TERMINAL_STATES, CaseTransition, POCase, POCaseId

_NOT_A_QUESTION = "not exercised by a case question"


def _position(case: POCase) -> CursorPosition:
    assert case.created_at is not None
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


@dataclass
class InMemoryPOCases:
    cases: list[POCase] = field(default_factory=list)

    def seed(self, cases: Iterable[POCase]) -> None:
        self.cases.extend(cases)

    def _visible(self, context: AccessContext) -> list[POCase]:
        # What RLS returns: the context's tenant and workspace, nothing else.
        return [
            case
            for case in self.cases
            if case.tenant_id.value == context.tenant_id
            and case.workspace_id.value == context.workspace_id
        ]

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        found = [
            case
            for case in self._visible(context)
            if (case_filter.state is None or case.state is case_filter.state)
            and (
                case_filter.supplier_name is None or case.supplier_name == case_filter.supplier_name
            )
            and not (case_filter.active_only and case.state in TERMINAL_STATES)
        ]
        found.sort(key=lambda c: (c.created_at, c.id.value), reverse=True)
        if request.after is not None:
            after = request.after
            found = [
                c
                for c in found
                if (c.created_at, c.id.value) < (after.sort_value, after.tiebreaker)
            ]
        return build_page(found[: request.fetch_limit], request=request, position_of=_position)

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        return sorted({case.supplier_name for case in self._visible(context)})

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        wanted = po_reference.strip(PO_REFERENCE_PADDING).lower()
        return sorted(
            (
                case
                for case in self._visible(context)
                if case.po_reference is not None
                and case.po_reference.strip(PO_REFERENCE_PADDING).lower() == wanted
            ),
            key=lambda c: c.po_reference or "",
        )

    async def add(self, context: AccessContext, case: POCase) -> None:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def save(self, context: AccessContext, case: POCase, *, audit: Any = None) -> None:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def get_current_state_entered_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId
    ) -> list[CaseTransition]:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def list_active(self, context: AccessContext) -> list[POCase]:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        raise NotImplementedError(_NOT_A_QUESTION)

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime
    ) -> list[tuple[POCaseId, CaseTransition]]:
        raise NotImplementedError(_NOT_A_QUESTION)
