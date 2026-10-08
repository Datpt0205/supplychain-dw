"""Ports this context needs, declared BY the consumer.

The composition root satisfies them. Declaring them here rather than importing a
concrete adapter is what keeps the handler testable without infrastructure.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_kernel.pagination import Page, PageQuery, PageRequest
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.domain.daily_brief import ClosedRound
from dw_supply_chain.domain.delay_impact import DelayImpactAnalysis
from dw_supply_chain.domain.follow_up import (
    FollowUpDue,
    FollowUpKey,
    FollowUpKind,
    FollowUpStatus,
    follow_up_key,
)
from dw_supply_chain.domain.packaging_design import PackagingDesign, PackagingHistoryEntry
from dw_supply_chain.domain.po_case import (
    TERMINAL_STATES,
    CaseState,
    CaseTransition,
    POCase,
    POCaseId,
)
from dw_supply_chain.domain.product_development_case import (
    ProductCaseTransition,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SampleRound,
)
from dw_supply_chain.domain.product_proposal import DraftClaim, ProposalDraft, ProposalField
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

    async def add(self, context: AccessContext, case: POCase, *, audit: AuditEvent) -> None:
        """`audit` commits in the same transaction as the case (ticket P2)."""
        ...

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None: ...
    async def save(self, context: AccessContext, case: POCase, *, audit: AuditEvent) -> None:
        """`audit` commits in the same transaction as the step: every write
        through this port names its audit event (ticket P2). A PO reference
        already taken in the tenant is a `ConflictError`."""
        ...

    async def get_sla_clock_started_at(
        self, context: AccessContext, case_id: POCaseId
    ) -> datetime | None:
        """When the SLA clock of the case's current state started: its latest
        entry into `sla_clock_start_state(state)` — the current state itself,
        except while receiving goods, whose clock started at payment (step
        17). `None` if it has never transitioned (still in `PO_CREATED` with no
        history row); the caller falls back to `POCase.created_at`, same shape
        `missing_update_status`'s own reference-point fallback already uses."""
        ...

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        """The caller's tenant's cases matching `case_filter`, newest first —
        the Case Workspace's own entry point, not scoped to one case the way
        every other list on this port is."""
        ...

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId, request: PageRequest
    ) -> Page[CaseTransition]:
        """One page of the case's own timeline, newest first, so the first
        page is where the case is now; a client shows each page oldest at
        the top. Paged because a case's history has no bound of its own."""
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

    async def bulk_sla_clock_started_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        """`get_sla_clock_started_at`, for many cases in one round trip
        — a case with no entry here has never transitioned, same as that
        method's own `None` case; the caller falls back to `POCase.
        created_at` per case, same fallback `GetSLAEvaluation` already uses
        for one case at a time. Called with one page of cases at most
        (`handlers.ACTIVE_CASES_PAGE`), so the ids it binds are bounded."""
        ...

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        """The caller's cases among `case_ids`, in no particular order — an id
        that is not the caller's, or not a case at all, is simply absent."""
        ...

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime, *, limit: int
    ) -> tuple[int, list[tuple[POCaseId, CaseTransition]]]:
        """How many cases moved at or after `since`, and the `limit` that
        moved most recently, each with its latest transition — what the
        daily brief reports as changed. The count is every one; the list is
        bounded, as the brief shows only a few."""
        ...


class SupplierUpdateRepositoryPort(Protocol):
    """Persists `SupplierUpdate`. Immutable records — no `save`, only `add`."""

    async def add(
        self, context: AccessContext, update: SupplierUpdate, *, audit: AuditEvent
    ) -> None:
        """`audit` commits in the same transaction as the record (ticket P2)."""
        ...

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

    async def add(
        self, context: AccessContext, analysis: DelayImpactAnalysis, *, audit: AuditEvent
    ) -> None:
        """`audit` commits in the same transaction as the record (ticket P2)."""
        ...

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
    @property
    def required_scope(self) -> str | None: ...


class PendingApprovalsPort(Protocol):
    """Undecided approvals, read from the platform's approval inbox.

    Declared here, satisfied by `dw_platform` at the composition root — this
    context never reads `platform.approval_requests` itself. The caller's
    tenant and workspace only, as the inbox itself reads them: the brief must
    not show an approval the inbox would refuse."""

    async def list_pending_by_type_prefix(
        self,
        context: AccessContext,
        *,
        prefix: str,
        limit: int,
        payload_match: tuple[str, str] | None = None,
    ) -> tuple[int, Sequence[PendingApprovalRecord]]:
        """How many pending approvals have an `approval_type` starting with
        `prefix`, and the newest `limit` of them. `prefix` is matched
        literally: an `_` in it is not a wildcard. `payload_match` (key,
        value) narrows both to those whose payload's top-level `key` is
        `value`: one PO case's approvals, filtered by the server."""
        ...

    async def pending_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> PendingApprovalRecord | None:
        """The newest pending approval of exactly `approval_type` whose
        payload's top-level `key` equals `value`: a product case's BGĐ
        review, by `product_dev_case_id`. None when there is none, and None
        when the caller may neither decide it nor asked for it (platform
        ADR 0004 amendment): what a case page shows a person."""
        ...


class RaisedApprovalsPort(Protocol):
    """Whether this context already raised an approval: bookkeeping for the
    code that raises it (`EnsureProductApproval`), never shown to a person, so not
    narrowed by who is asking. A worker sweep has no scopes; filtered, it
    would find nothing and raise the review again."""

    async def raised_by_payload(
        self, context: AccessContext, *, approval_type: str, key: str, value: str
    ) -> PendingApprovalRecord | None:
        """The newest pending approval of exactly `approval_type` in the
        caller's workspace whose payload's top-level `key` equals `value`."""
        ...


class ReviewRaise(StrEnum):
    """What asking for the approval a case waits on (BGĐ's review at step 6,
    the sign-off at step 9) did."""

    # This call raised the approval (and told the people who may decide it).
    RAISED = "raised"
    # The case's review was already waiting for a decision.
    ALREADY_PENDING = "already_pending"
    # No approval exists after the attempt: the run was refused (plan quota,
    # spend ceiling), failed, or is still being raised elsewhere. The case
    # waits in `pending_bod_review` or `pending_signoff` and the reconcile lane
    # tries again.
    NOT_RAISED = "not_raised"
    # The case is not waiting for an approval; nothing to raise.
    NOT_WAITING = "not_waiting"


@dataclass(frozen=True, slots=True)
class ReviewRequester:
    """Who asks for the review, with the authority the run is stamped with
    (`worker_runs.requested_by` and `actor_*`): the requester of the approval,
    whom the strict prefix keeps from deciding it."""

    principal_id: uuid.UUID
    roles: frozenset[str]
    scopes: frozenset[str]
    plan_id: str
    channel: str

    @classmethod
    def from_context(cls, context: AccessContext) -> ReviewRequester:
        return cls(
            principal_id=context.principal_id,
            roles=context.roles,
            scopes=context.scopes,
            plan_id=context.plan_id,
            channel="web",
        )


class ProductApprovalPort(Protocol):
    """Raises the approval a case waits on: BGĐ's review once per sample
    round, the sign-off once per sign-off round
    (`application.product_reviews.EnsureProductApproval`)."""

    async def ensure(
        self, context: AccessContext, case: ProductDevelopmentCase, requester: ReviewRequester
    ) -> ReviewRaise: ...


class ReviewRunStarterPort(Protocol):
    """Starts the review graph's run. `dw_agent_runtime`'s runner satisfies it;
    `uq_worker_runs_active_thread` refuses a second unfinished run on one
    thread with a `ConflictError` naming the thread."""

    async def start(
        self, *, run_context: RunContext, input_payload: dict[str, Any]
    ) -> uuid.UUID: ...


class ProductCaseReviewPort(Protocol):
    """What an approval graph (BGĐ's review, the sign-off) does to a case once
    a decision is in: read it, save the outcome's step, or record that it
    applied nothing because the
    case had moved on. `SqlProductCaseRepository` satisfies it."""

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None: ...

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None: ...

    async def append_audit(self, context: AccessContext, audit: AuditEvent) -> None: ...


class ReviewNotifierPort(Protocol):
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


class WorkspacesAwaitingApprovalPort(Protocol):
    """The one cross-tenant read the review reconcile lane needs: which
    (tenant, workspace) pairs hold a product case waiting for BGĐ's review or
    for its sign-off. Ids only."""

    async def awaiting_approval(self) -> list[tuple[uuid.UUID, uuid.UUID]]: ...


class TenantPlanPort(Protocol):
    """The plan a tenant is on, read by the platform: what a run the lane
    starts is counted against, as a run a person starts is."""

    async def plan_of(self, tenant_id: uuid.UUID) -> str | None: ...


@dataclass(frozen=True, slots=True)
class FollowUpDraft:
    """A follow-up about to open: what is due, with its id, the recipient
    scopes stamped from the tenant's policy at this moment, and the PIC
    stamped beside them when the policy routes the kind to `pic` and the PIC
    is a member of the case's workspace now (None otherwise)."""

    id: uuid.UUID
    due: FollowUpDue
    recipient_scopes: frozenset[str]
    recipient_user_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class FollowUpRecord:
    id: uuid.UUID
    case_kind: CaseKind
    case_id: uuid.UUID
    workspace_id: uuid.UUID
    # The PO reference (None while the case awaits its PO, ADR 0017), or a
    # product case's proposal code.
    reference: str | None
    # None for a product case before its sample is requested.
    supplier_name: str | None
    kind: FollowUpKind
    episode: str
    milestone: str | None
    days: int
    limit_days: int | None
    recipient_scopes: frozenset[str]
    recipient_user_id: uuid.UUID | None
    status: FollowUpStatus
    opened_at: datetime
    notified_at: datetime | None
    closed_at: datetime | None
    closed_by: uuid.UUID | None
    close_note: str | None

    @property
    def key(self) -> FollowUpKey:
        return follow_up_key(self.case_kind, self.case_id, self.kind, self.episode)

    def handed_to(self, principal_id: uuid.UUID, scopes: frozenset[str]) -> bool:
        """Whether a caller is one of those it was handed to: a holder of a
        stamped scope, or the stamped PIC. Who may close it, and whose it is."""
        return bool(self.recipient_scopes & scopes) or principal_id == self.recipient_user_id


class FollowUpRepositoryPort(Protocol):
    async def open(
        self,
        context: AccessContext,
        drafts: Sequence[FollowUpDraft],
        *,
        audit: Callable[[FollowUpDraft], AuditEvent],
    ) -> int:
        """Open each draft unless its case, kind and episode already has a
        follow-up (open or closed): an episode opens once. Each draft that
        opened has `audit(draft)` written in the same transaction; one that did
        not open writes none. How many opened."""
        ...

    async def list_open(self, context: AccessContext) -> list[FollowUpRecord]:
        """The open follow-ups of the context's workspace, newest first."""
        ...

    async def resolve(
        self,
        context: AccessContext,
        follow_ups: Sequence[FollowUpRecord],
        *,
        audit: Callable[[FollowUpRecord], AuditEvent],
    ) -> int:
        """Close these follow-ups as resolved, their signal gone, each still
        open with `audit(record)` in the same transaction; one already closed
        is left as it is and audited by nobody. How many closed."""
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


class WorkspacesWithCasesPort(Protocol):
    """The one cross-tenant read the follow-up sweep needs: which workspaces
    to visit, as (tenant_id, workspace_id), every workspace holding a PO case
    or a product case. Ids only."""

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]: ...


class ScopeHoldersPort(Protocol):
    """Members of a workspace who hold any of `scopes`, through a role or a
    permission set. Declared here, satisfied by `dw_platform`. `tenant_id`
    comes from a verified source (the access context, a tenant-tagged row,
    a stamped state), never from a request body."""

    async def holding(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, scopes: frozenset[str]
    ) -> list[uuid.UUID]: ...


class WorkspaceMembersPort(Protocol):
    """Which of `user_ids` are members of a workspace of an active tenant now:
    a PIC is told about a case, and handed one, only while they are.
    Declared here, satisfied by `dw_platform`."""

    async def members(
        self, tenant_id: uuid.UUID, workspace_id: uuid.UUID, user_ids: frozenset[uuid.UUID]
    ) -> frozenset[uuid.UUID]: ...


class ActiveProductCasesPort(Protocol):
    """What the follow-up sweep reads of product cases: those of the context's
    workspace not yet ordered or cancelled, and when each last moved."""

    async def list_active(
        self, context: AccessContext, request: PageRequest
    ) -> Page[ProductDevelopmentCase]:
        """One page of them, newest first: a caller reads every page, each
        bounded, so no query binds one parameter per active case."""
        ...

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """When each case made its latest transition (into the state it is in
        now, a resume included): where its SLA clock starts, as a PO case's
        does (`POCaseRepositoryPort.bulk_sla_clock_started_at`)."""
        ...


class StageOneReadsPort(ActiveProductCasesPort, Protocol):
    """What the daily brief and the stage-1 daily report read of product
    cases (ticket 08): the active ones, as the sweep reads them, and the
    sample rounds closed since a moment, each with its case (a rejected
    sample's case is cancelled, no longer active)."""

    async def closed_rounds_since(
        self, context: AccessContext, since: datetime
    ) -> list[ClosedRound]:
        """Rounds of `context`'s workspace closed at or after `since`, oldest
        first."""
        ...


class ProductCaseLookupPort(Protocol):
    """Opening a product case by its proposal code from a question."""

    async def find_by_proposal_code(
        self, context: AccessContext, proposal_code: str
    ) -> list[ProductDevelopmentCase]:
        """The cases of `context`'s workspace whose code equals
        `proposal_code`, case-insensitively and ignoring surrounding spaces; a
        case of another tenant or workspace is never returned."""
        ...


class NamedMember(Protocol):
    """One person of a workspace, as a question names them."""

    @property
    def user_id(self) -> uuid.UUID: ...

    @property
    def display_name(self) -> str: ...


class WorkspaceDirectoryPort(Protocol):
    """The people of the caller's workspace, so a PIC named in a question
    resolves to one of them. Declared here, satisfied by `dw_platform`'s
    workspace directory."""

    async def list_members(self, context: AccessContext) -> Sequence[NamedMember]: ...


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


@dataclass(frozen=True, slots=True)
class NewCaseDocument:
    """A document about to be recorded on a case: everything but its version,
    which the insert computes, and its upload time, which the database stamps.
    `case_kind` selects which of the table's two case columns `case_id` goes
    in, and the key's kind segment says the same (`ck_case_documents_object_key`)."""

    id: CaseDocumentId
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    object_key: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str


class CaseDocumentRepositoryPort(Protocol):
    """Records of case documents. Append-only: no update, no delete."""

    async def add(
        self, context: AccessContext, document: NewCaseDocument, *, audit: AuditEvent
    ) -> CaseDocument:
        """Insert with `version` one past the case's highest for this type,
        and `audit` in the same transaction. `ConflictError` when a concurrent
        upload took that version first; the client retries."""
        ...

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[CaseDocument]:
        """The case's documents, by type and then newest version first."""
        ...

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        """The caller's document, or None: another tenant's or another
        workspace's reads as absent."""
        ...


class CaseDocumentStoragePort(Protocol):
    """The bytes of case documents, by the key the server built (ADR 0021).
    Declared here, satisfied by an adapter on the deployment's S3 client."""

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes:
        """`NotFoundError` when no object has this key."""
        ...

    async def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    last_modified: datetime


class CaseDocumentObjectListingPort(Protocol):
    """What the orphan sweep reads from the bucket, a page at a time."""

    async def list_after(
        self, prefix: str, *, start_after: str | None, limit: int
    ) -> list[StoredObject]:
        """Up to `limit` objects under `prefix`, in key order, after
        `start_after` (from the beginning when None)."""
        ...

    async def delete(self, key: str) -> None: ...


class CaseDocumentKeysPort(Protocol):
    """The one question the orphan sweep asks the database, under the
    tenant and workspace its keys name."""

    async def existing_keys(self, context: AccessContext, keys: Sequence[str]) -> set[str]:
        """Which of `keys` a document row of `context`'s workspace holds."""
        ...


@dataclass(frozen=True, slots=True)
class ProductCaseListFilter:
    """What narrows the product-case list; every field an exact match and the
    default narrows nothing. The PIC filter only ever narrows: every holder of
    the read scope sees every case of the workspace (QE-18)."""

    state: ProductDevState | None = None
    pic_user_id: uuid.UUID | None = None
    # A Category key of the tenant's list, as cases are stamped (ticket 08:
    # what a question narrows by).
    category: str | None = None

    def page_query(self, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> PageQuery:
        """The cursor identity for listing with THIS filter, in this workspace:
        a cursor minted under one filter or workspace is refused under another.
        A field left at its default is omitted, as `POCaseListFilter` does."""
        narrowing = {
            f.name: value for f in fields(self) if (value := getattr(self, f.name)) != f.default
        }
        return PageQuery(
            key="supply_chain.product_dev_cases",
            filters={"tenant": tenant_id, "workspace": workspace_id, **narrowing},
        )


class ProductCaseRepositoryPort(Protocol):
    """Persists `ProductDevelopmentCase`, its history and its sample rounds.

    `add` and `save` drain the case's pending steps into history rows, open or
    close the rounds those steps name, and append `audit`, all in the
    transaction that writes the state. `save` is optimistic on `version`."""

    async def add(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        *,
        audit: AuditEvent,
        consume: DraftClaim | None = None,
    ) -> None:
        """`ConflictError` when the tenant already has the proposal code.

        With `consume`, the chat draft the case is made from is deleted in the
        same transaction, guarded on its version being the summarised one and
        unexpired; `ProposalDraftChangedError` (and no case) when it is not."""
        ...

    async def get(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> ProductDevelopmentCase | None:
        """The caller's case, with its current round's opening time; another
        tenant's or workspace's reads as absent."""
        ...

    async def save(
        self, context: AccessContext, case: ProductDevelopmentCase, *, audit: AuditEvent
    ) -> None:
        """`ConflictError` when another write moved the case first, or when a
        round the step closes is already closed."""
        ...

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: ProductCaseListFilter
    ) -> Page[ProductDevelopmentCase]:
        """The workspace's cases matching the filter, newest first."""
        ...

    async def list_transitions(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId, request: PageRequest
    ) -> Page[ProductCaseTransition]:
        """One page of the case's history, newest first (as
        `POCaseRepositoryPort.list_transitions`)."""
        ...

    async def list_rounds(
        self, context: AccessContext, case_id: ProductDevelopmentCaseId
    ) -> list[SampleRound]:
        """The case's sample rounds, first round first."""
        ...

    async def place_order(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        po_case: POCase,
        *,
        audits: Sequence[AuditEvent],
    ) -> None:
        """ĐẶT HÀNG in one transaction: the case moved out of the state its
        step left, at the version read, or a `ConflictError` and nothing
        written; then its history row, `po_case` with its lines, and `audits`
        (ADR 0017)."""
        ...

    async def po_case_of(self, context: AccessContext, case_id: uuid.UUID) -> uuid.UUID | None:
        """The PO case ĐẶT HÀNG opened from the case, or None."""
        ...

    async def state_entered_at(
        self, context: AccessContext, case_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """When each case made its latest transition: its SLA clock's start
        (`ActiveProductCasesPort.state_entered_at`, the same answer)."""
        ...


class ProposalDraftRepositoryPort(Protocol):
    """The caller's own open chat proposal (zalo-channel ticket 04).

    Keyed by the context: tenant and workspace by RLS and by name, the person by
    `context.principal_id`. No method takes a user id, so no caller can read or
    write somebody else's draft."""

    async def open_draft(self, context: AccessContext, channel: str) -> ProposalDraft | None: ...

    async def put(
        self,
        context: AccessContext,
        channel: str,
        *,
        fields: Mapping[ProposalField, str],
        draft_version: int,
        summarized_version: int | None,
        expires_at: datetime,
        previous_version: int | None,
    ) -> ProposalDraft:
        """Create the draft (`previous_version=None`) or replace the one at
        `previous_version`; `ProposalDraftChangedError` when another write got
        there first."""
        ...

    async def discard(self, context: AccessContext, channel: str) -> bool:
        """Delete the caller's draft; False when there was none."""
        ...


class PackagingDesignReaderPort(Protocol):
    """A PO case's step-12 sub-flow, if any step of it was taken; read under
    the caller's tenant and workspace. All step 13's gate asks."""

    async def get(
        self, context: AccessContext, po_case_id: uuid.UUID
    ) -> PackagingDesign | None: ...


class PackagingDesignRepositoryPort(PackagingDesignReaderPort, Protocol):
    """Persists `PackagingDesign` (slice PK)."""

    async def history(
        self, context: AccessContext, po_case_id: uuid.UUID
    ) -> list[PackagingHistoryEntry]:
        """Every step taken, oldest first."""
        ...

    async def save(
        self,
        context: AccessContext,
        design: PackagingDesign,
        *,
        actor_id: uuid.UUID,
        audit: AuditEvent,
    ) -> None:
        """The row, its pending history rows and `audit` in one transaction,
        and only while the PO case is still in `pre_production` (checked in
        that transaction). Optimistic on `version`; the first step inserts.
        Either refusal is a `ConflictError`."""
        ...
