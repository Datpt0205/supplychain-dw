"""In-memory product-case reads for a question and for the daily brief.

`InMemoryProductCases` keeps the promise RLS keeps for
`SqlProductCaseRepository`: a caller sees only the cases (and rounds) of its
own tenant AND workspace, so another tenant's or another workspace's row is
simply absent — never "found but forbidden". `InMemoryDirectory` is the
workspace directory with the same narrowing. Shared by the eval graders
(`eval_graders`) and the unit tests of the command bar, the Zalo question
command and the brief. The writes and the reads neither runs raise
`NotImplementedError` rather than pretend.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.ports import ProductCaseListFilter
from dw_supply_chain.domain.daily_brief import ClosedRound
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.product_development_case import (
    PRODUCT_TERMINAL_STATES,
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    SampleRound,
)
from dw_supply_chain.testing.pages import case_position, newest_first_page

_NOT_READ_HERE = "not exercised by a question or a brief"


def _position(case: ProductDevelopmentCase) -> CursorPosition:
    assert case.created_at is not None
    return CursorPosition(sort_value=case.created_at, tiebreaker=case.id.value)


def _sees(context: AccessContext, case: ProductDevelopmentCase) -> bool:
    # What RLS returns: the context's tenant and workspace, nothing else.
    return (
        case.tenant_id.value == context.tenant_id
        and case.workspace_id.value == context.workspace_id
    )


@dataclass
class InMemoryProductCases:
    cases: list[ProductDevelopmentCase] = field(default_factory=list)
    # When each case entered its current state; `created_at` when absent.
    entered: dict[uuid.UUID, datetime] = field(default_factory=dict)
    rounds: list[tuple[uuid.UUID, SampleRound]] = field(default_factory=list)

    def seed(self, cases: Iterable[ProductDevelopmentCase]) -> None:
        self.cases.extend(cases)

    def seed_round(self, case: ProductDevelopmentCase, sample_round: SampleRound) -> None:
        self.rounds.append((case.id.value, sample_round))

    def _visible(self, context: AccessContext) -> list[ProductDevelopmentCase]:
        return [case for case in self.cases if _sees(context, case)]

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        found = [
            case
            for case in self._visible(context)
            if (case_filter.state is None or case.state is case_filter.state)
            and (case_filter.pic_user_id is None or case.pic_user_id == case_filter.pic_user_id)
            and (case_filter.category is None or case.category == case_filter.category)
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

    async def find_by_proposal_code(
        self, context: AccessContext, proposal_code: str
    ) -> list[ProductDevelopmentCase]:
        wanted = proposal_code.strip().lower()
        return sorted(
            (c for c in self._visible(context) if c.proposal_code.strip().lower() == wanted),
            key=lambda c: c.proposal_code,
        )

    async def list_active(
        self, context: AccessContext, request: PageRequest
    ) -> Page[ProductDevelopmentCase]:
        active = [c for c in self._visible(context) if c.state not in PRODUCT_TERMINAL_STATES]
        return newest_first_page(active, request, case_position)

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        visible = {c.id.value for c in self._visible(context)}
        return {
            case_id: at
            for case_id, at in self.entered.items()
            if case_id in visible and case_id in case_ids
        }

    async def closed_rounds_since(
        self, context: AccessContext, since: datetime
    ) -> list[ClosedRound]:
        cases = {c.id.value: c for c in self._visible(context)}
        closed = [
            (sample_round.closed_at, ClosedRound(case=cases[case_id], sample_round=sample_round))
            for case_id, sample_round in self.rounds
            if case_id in cases
            and sample_round.closed_at is not None
            and sample_round.closed_at >= since
        ]
        return [entry for _, entry in sorted(closed, key=lambda pair: pair[0])]

    async def add(self, context: AccessContext, case: ProductDevelopmentCase, **_: Any) -> None:
        raise NotImplementedError(_NOT_READ_HERE)

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        raise NotImplementedError(_NOT_READ_HERE)

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: Any = None
    ) -> None:
        raise NotImplementedError(_NOT_READ_HERE)

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        raise NotImplementedError(_NOT_READ_HERE)

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        raise NotImplementedError(_NOT_READ_HERE)

    async def place_order(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        po_case: POCase,
        *,
        audits: Sequence[Any],
    ) -> None:
        raise NotImplementedError(_NOT_READ_HERE)

    async def po_case_of(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        raise NotImplementedError(_NOT_READ_HERE)


@dataclass(frozen=True, slots=True)
class Member:
    user_id: uuid.UUID
    display_name: str
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID


@dataclass
class InMemoryDirectory:
    """The people of each workspace; a caller lists only its own."""

    members: list[Member] = field(default_factory=list)

    async def list_members(self, context: AccessContext) -> list[Member]:
        return [
            m
            for m in self.members
            if m.tenant_id == context.tenant_id and m.workspace_id == context.workspace_id
        ]
