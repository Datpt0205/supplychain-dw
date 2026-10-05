"""Ports this context needs, declared BY the consumer.

The composition root satisfies them. Declaring them here rather than importing a
concrete adapter is what keeps the handler testable without infrastructure.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Protocol

from dw_kernel.pagination import Page, PageQuery, PageRequest
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.delay_impact import DelayImpactAnalysis
from dw_supply_chain.domain.follow_up import FollowUpDue, FollowUpKind, FollowUpStatus
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.supplier_update import SupplierUpdate, SupplierUpdateId


@dataclass(frozen=True, slots=True)
class POCaseListFilter:
    """What narrows `POCaseRepositoryPort.list_page` — every field is an
    exact match, and the default narrows nothing.

    `supplier_name` matches exactly as stored, the same identity
    `domain.portfolio` groups the Control Tower's supplier rows by, so a
    drill-down from a row lists exactly the cases that row counted.
    `active_only` leaves "which states are terminal" to `TERMINAL_STATES`
    on the server rather than asking a client to send that list.
    """

    state: CaseState | None = None
    supplier_name: str | None = None
    active_only: bool = False

    @property
    def matches_nothing(self) -> bool:
        """`active_only` with a terminal `state` asks for an empty set by
        definition — reachable in two clicks (a supplier drill-down, then
        "Hoàn tất" in the state picker). Postgres cannot see the
        contradiction and walks every terminal row to return nothing
        (measured: 175 685 rows for one 200k-case tenant), so the handler
        answers it without a query."""
        return self.active_only and self.state in TERMINAL_STATES

    def as_page_filters(self) -> dict[str, object]:
        """Every field that narrows, for `PageQuery.filters` — derived rather
        than listed, so a field added here joins the cursor's fingerprint
        without anyone having to remember that it must (a cursor from one
        filter replayed against another would otherwise resume in the wrong
        window).

        A field left at its default is omitted, not sent as `None`: the
        fingerprint stringifies values, so `supplier_name=None` and a real
        supplier literally named "None" would otherwise fingerprint alike.
        Holds for scalar fields only — a set-valued field would stringify in
        hash order, which differs between processes, so one worker's cursor
        would be refused by the next; give such a field a sorted form first."""
        return {
            field.name: value
            for field in fields(self)
            if (value := getattr(self, field.name)) != field.default
        }

    def page_query(self, tenant_id: uuid.UUID) -> PageQuery:
        """The cursor identity for listing with THIS filter. `ListPOCases`
        decodes every incoming cursor against it, from the same object it
        hands the repository — the pairing is that handler's structure, not
        a convention its callers have to keep."""
        return PageQuery(
            key="supply_chain.po_cases", filters={"tenant": tenant_id, **self.as_page_filters()}
        )


# What `find_by_reference` strips from both ends, of the stored reference
# and of the one asked for — the same set in SQL and in every implementation.
PO_REFERENCE_PADDING = " \t\r\n\u00a0"


class POCaseRepositoryPort(Protocol):
    """Persists `POCase`.

    Every method takes the caller's verified `context` and re-derives its own
    tenant-scoped session from it — shape mirrors `dw_platform`'s
    `SqlMembershipAdminRepository`, which this context has no multi-repository
    unit of work to share, unlike `SqlApprovalRepository`'s. `save` enforces
    optimistic concurrency on `version` itself rather than trusting a caller
    not to race. `save`/`add` also persist whatever `case.pop_pending_
    transitions()` has accumulated, in the same transaction as the state
    write — the SLA evaluator's own history, never a second place a caller
    has to remember to write to.
    """

    async def add(self, context: AccessContext, case: POCase) -> None: ...
    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None: ...
    async def save(self, context: AccessContext, case: POCase) -> None: ...
    async def get_current_state_entered_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        """When the case entered its CURRENT state, or `None` if it has never
        transitioned (still in `PO_CREATED` with no history row) — the
        caller falls back to `POCase.created_at` in that case, same shape
        `missing_update_status`'s own reference-point fallback already
        uses."""
        ...

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        """The caller's tenant's cases matching `case_filter`, newest first —
        the Case Workspace's own entry point, not scoped to one case the way
        every other list on this port is."""
        ...

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId
    ) -> list[CaseTransition]:
        """The case's own timeline, oldest first — a journey reads start to
        now, unlike every other list here, which reads newest first because
        it is a feed of recent activity rather than a sequence."""
        ...

    async def list_active(self, context: AccessContext) -> list[POCase]:
        """Every case NOT in a terminal state, unpaginated — the entry point
        of `handlers.assess_active_cases`, which the Attention Queue and the
        Control Tower both compute over. Deliberately not exposed as a
        public list endpoint the way `list_page` is: its callers need every
        active case to compute signals over, not a page a client scrolls
        through."""
        ...

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        """Every distinct supplier name on the caller's tenant's cases, as
        stored — what a case query resolves a supplier mention AGAINST, so a
        name the model produced never reaches a filter unless it is one of
        these."""
        ...

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        """Cases whose `po_reference` equals `po_reference` ignoring case and
        any `PO_REFERENCE_PADDING` around either side — stored references are
        kept as typed, and a pasted one can carry a tab or a no-break space.
        A list, not one: the stored uniqueness is case-sensitive, so "po-1"
        and "PO-1" can both exist and a lookup that picked one would be a
        guess."""
        ...

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        """`get_current_state_entered_at`, for many cases in one round trip
        — a case with no entry here has never transitioned, same as that
        method's own `None` case; the caller falls back to `POCase.
        created_at` per case, same fallback `GetSLAEvaluation` already uses
        for one case at a time."""
        ...

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        """The caller's cases among `case_ids`, in no particular order — an id
        that is not the caller's, or not a case at all, is simply absent."""
        ...

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime
    ) -> list[tuple[POCaseId, CaseTransition]]:
        """Each case's most recent transition at or after `since`, one per
        case that moved at all — what the daily brief reports as changed.
        Bounded by one window's activity rather than paged, the same shape
        `list_active` has."""
        ...


class SupplierUpdateRepositoryPort(Protocol):
    """Persists `SupplierUpdate`. Immutable records — no `save`, only `add`."""

    async def add(self, context: AccessContext, update: SupplierUpdate) -> None: ...
    async def get(
        self, context: AccessContext, update_id: SupplierUpdateId
    ) -> SupplierUpdate | None: ...
    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[SupplierUpdate]: ...

    async def bulk_latest(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, SupplierUpdate]:
        """Each case's most recent update, for many cases in one round trip —
        a case with no entry has never received one, same as
        `GetMissingUpdateStatus`'s own `None` case for a single case. The
        whole record, not its timestamp: how long the supplier has been
        silent and whether their last word reported a delay are two readings
        of the same row, and reading them from two queries is how they would
        come to disagree."""
        ...


class DelayImpactAnalysisRepositoryPort(Protocol):
    """Persists `DelayImpactAnalysis`. Immutable records — no `save`, only `add`."""

    async def add(self, context: AccessContext, analysis: DelayImpactAnalysis) -> None: ...
    async def list_for_case(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[DelayImpactAnalysis]: ...


class PendingApprovalRecord(Protocol):
    """The parts of a platform approval request this context reads. A
    Protocol rather than an import: the platform's own `ApprovalRequest`
    satisfies it by shape, the same way `PlanEntitlementService` satisfies
    the runtime's `RunAllowancePort`."""

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def approval_type(self) -> str: ...
    @property
    def payload(self) -> Mapping[str, object]: ...
    @property
    def created_at(self) -> datetime | None: ...


class PendingApprovalsPort(Protocol):
    """Undecided approvals, read from the platform's approval inbox.

    Declared here, satisfied by `dw_platform` at the composition root — this
    context never reads `platform.approval_requests` itself."""

    async def list_pending_by_type_prefix(
        self, context: AccessContext, *, prefix: str, limit: int
    ) -> tuple[int, Sequence[PendingApprovalRecord]]:
        """How many pending approvals have an `approval_type` starting with
        `prefix`, and the newest `limit` of them. `prefix` is matched
        literally: an `_` in it is not a wildcard."""
        ...


@dataclass(frozen=True, slots=True)
class FollowUpDraft:
    """A follow-up about to open: what is due, with its id and the recipient
    scopes stamped from the tenant's policy at this moment."""

    id: uuid.UUID
    due: FollowUpDue
    recipient_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class FollowUpRecord:
    id: uuid.UUID
    po_case_id: uuid.UUID
    workspace_id: uuid.UUID
    po_reference: str
    supplier_name: str
    kind: FollowUpKind
    episode: str
    milestone: str | None
    days: int
    limit_days: int | None
    recipient_scopes: frozenset[str]
    status: FollowUpStatus
    opened_at: datetime
    notified_at: datetime | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    close_note: str | None

    @property
    def key(self) -> tuple[str, FollowUpKind, str]:
        return (str(self.po_case_id), self.kind, self.episode)


class FollowUpRepositoryPort(Protocol):
    async def open(self, context: AccessContext, drafts: Sequence[FollowUpDraft]) -> int:
        """Open each draft unless its case, kind and episode already has a
        follow-up (open or closed): an episode opens once. How many opened."""
        ...

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        """The tenant's open follow-ups, newest first."""
        ...

    async def resolve(self, context: AccessContext, follow_up_ids: Sequence[uuid.UUID]) -> None:
        """Close these open follow-ups as resolved: their signal is gone."""
        ...

    async def mark_notified(self, context: AccessContext, follow_up_id: uuid.UUID) -> None: ...

    async def get(
        self, context: AccessContext, follow_up_id: uuid.UUID
    ) -> FollowUpRecord | None: ...

    async def close_done(
        self,
        context: AccessContext,
        follow_up_id: uuid.UUID,
        *,
        note: str | None,
        audit: AuditEvent,
    ) -> bool:
        """Close an open follow-up as done by the caller, with the audit
        event in the same transaction. False if it is not open."""
        ...


class TenantsWithCasesPort(Protocol):
    """The one cross-tenant read the follow-up sweep needs: which tenants to
    visit, as (tenant_id, a workspace_id of theirs). Ids only."""

    async def tenants(self) -> list[tuple[uuid.UUID, uuid.UUID]]: ...


class ScopeHoldersPort(Protocol):
    """Members of a workspace who hold any of `scopes`, through a role or a
    permission set. Declared here, satisfied by `dw_platform`."""

    async def holding(
        self, context: AccessContext, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]: ...


class FollowUpNotifierPort(Protocol):
    """Delivers one in-app message to people in `context`'s workspace, once
    per `source_key`. Declared here, satisfied by `dw_platform`'s inbox."""

    async def deliver(
        self,
        context: AccessContext,
        *,
        recipients: Sequence[uuid.UUID],
        source_key: str,
        title: str,
        body: str,
        link: str | None,
    ) -> None: ...
