"""Step 1 from a list: AI reads the PIC's list of proposed products into rows,
the PIC proposes or drops each one (ticket ai-automation/08).

Four doors, each deciding where its effect happens:

- **`UploadProposalList`** (route): the scopes proposing needs (the write and
  the tenant's `propose` duty, `ProposeProductCase.propose_scopes`, one answer
  for both), a type the file's own bytes agree with, at most 10 MiB. The file
  is stored as uploaded; nothing is read here.
- **`ReadProposalLists`**, the worker lane `supply_chain_proposal_lists`: ids
  from a definer queue; the list read under ITS tenant's and workspace's RLS
  and refused, with no call, when it is not there; the tenant's plan; text in
  process (an image or a scan is `unreadable`, never guessed); account numbers
  masked; one structured call with the text and the tenant's Category list as
  the prompt's one untrusted variable; then code grounds each row, keeps a
  Category only when it is one of the tenant's keys, and names what the
  workspace already holds (`domain.proposal_list`). One reading row per
  (list, prompt version); the uploader is told it is ready.
- **`ProposeFromList`** (route): the PIC proposes one row with the values they
  checked. The case is made by `ProposeProductCase.handle`, the one door that
  makes a case: the PIC is stamped from the caller, the Category must be the
  tenant's, a code already taken is the database's refusal. Then the row's
  decision is recorded, once.
- **`DropFromList`** (route): a row the PIC does not propose, once, with an
  optional reason.

Nothing here proposes on its own: a row becomes a case only when a person
presses for that row.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError, ModelRequest
from dw_kernel.errors import (
    ConflictError,
    DomainError,
    InfrastructureError,
    NotFoundError,
    PayloadTooLargeError,
    QuotaExceededError,
)
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent, lane_audit_event, system_actor
from dw_supply_chain.application.document_extraction import DocumentTextPort
from dw_supply_chain.application.handlers import PRODUCT_CASE_READ
from dw_supply_chain.application.ports import ReviewNotifierPort, TenantPlanPort
from dw_supply_chain.application.product_cases import ProposeProductCase
from dw_supply_chain.domain.case_document import accepted_content_type, clean_filename
from dw_supply_chain.domain.extraction import (
    ExtractionStatus,
    is_unreadable,
    redact_identifiers,
)
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.domain.product_proposal import ProposalOrigin
from dw_supply_chain.domain.proposal_list import (
    PROPOSAL_LIST_SPEC,
    ProposalListReading,
    TakenCodes,
    ground_rows,
    wanted_codes,
    with_findings,
)

logger = logging.getLogger(__name__)

PROPOSAL_LISTS_LANE = "supply_chain_proposal_lists"
PROPOSAL_LISTS_WORKER_ID = "supply_chain.proposal_lists"
PROPOSAL_LISTS_WORKER_VERSION = "1.0.0"
# The prompt's one untrusted variable: the list's text and the Category list.
PROPOSAL_LIST_VARIABLE = "proposal_list"
MAX_LIST_BYTES = 10 * 1024 * 1024
LIST_UPLOADED = "supply_chain.proposal_list.uploaded"
LIST_READ = "supply_chain.proposal_list.read"
ROW_DECIDED = "supply_chain.proposal_list.row_decided"
_RESOURCE = "proposal_list"
_REASON_MAX = 1000


def lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=system_actor(PROPOSAL_LISTS_LANE).value,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


class RowDecision(StrEnum):
    PROPOSED = "proposed"
    DROPPED = "dropped"


# --------------------------------------------------------------- records --


@dataclass(frozen=True, slots=True)
class ProposalList:
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploaded_by: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewProposalList:
    id: uuid.UUID
    filename: str
    content_type: str
    sha256: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ListReading:
    id: uuid.UUID
    list_id: uuid.UUID
    status: ExtractionStatus
    prompt_id: str
    prompt_version: str
    rows: tuple[Mapping[str, Any], ...]
    error: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NewListReading:
    id: uuid.UUID
    list_id: uuid.UUID
    status: ExtractionStatus
    prompt_id: str
    prompt_version: str
    model_profile: str | None
    rows: tuple[Mapping[str, Any], ...] = ()
    redactions: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RowDecisionRecord:
    row_index: int
    decision: RowDecision
    product_dev_case_id: uuid.UUID | None
    reason: str | None
    decided_by: uuid.UUID
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class NewRowDecision:
    id: uuid.UUID
    list_id: uuid.UUID
    row_index: int
    decision: RowDecision
    product_dev_case_id: uuid.UUID | None
    reason: str | None


# ------------------------------------------------------------------ ports --


class ProposalListRepositoryPort(Protocol):
    async def add(
        self, context: AccessContext, new: NewProposalList, *, audit: AuditEvent
    ) -> None: ...

    async def get(self, context: AccessContext, list_id: uuid.UUID) -> ProposalList | None: ...

    async def content(self, context: AccessContext, list_id: uuid.UUID) -> bytes | None: ...

    async def recent(self, context: AccessContext, limit: int) -> list[ProposalList]: ...

    async def latest_reading(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> ListReading | None: ...

    async def add_reading(
        self, context: AccessContext, reading: NewListReading, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the list already has a reading under
        this prompt version."""
        ...

    async def decisions(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> list[RowDecisionRecord]: ...

    async def decide(
        self, context: AccessContext, decision: NewRowDecision, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the row already has a decision."""
        ...


@dataclass(frozen=True, slots=True)
class QueuedList:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    list_id: uuid.UUID


class ProposalListQueuePort(Protocol):
    async def awaiting(self, prompt_ref: str, limit: int) -> list[QueuedList]: ...


class TakenCodesPort(Protocol):
    """What the caller's workspace already holds among the given codes and
    names (proposal codes and product names of its cases, item codes and
    SKU codes issued)."""

    async def taken(
        self,
        context: AccessContext,
        *,
        proposal_codes: Sequence[str],
        item_codes: Sequence[str],
        product_names: Sequence[str],
    ) -> TakenCodes: ...


class CategoryListPort(Protocol):
    """The tenant's Category keys and labels (its SLA policy's list)."""

    async def categories(self, context: AccessContext) -> list[tuple[str, str]]: ...


@dataclass(frozen=True)
class TenantCategories:
    """`CategoryListPort` over the list `propose` itself accepts: one owner."""

    propose: ProposeProductCase

    async def categories(self, context: AccessContext) -> list[tuple[str, str]]:
        return [(c.key, c.label) for c in await self.propose.categories(context)]


# ----------------------------------------------------------------- upload --


@dataclass(frozen=True)
class UploadProposalList:
    lists: ProposalListRepositoryPort
    propose: ProposeProductCase
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, *, filename: str, content_type: str, data: bytes
    ) -> ProposalList:
        for scope in sorted(await self.propose.propose_scopes(context)):
            await self.authz.require(context=context, action=scope, resource_type=_RESOURCE)
        if len(data) > MAX_LIST_BYTES:
            raise PayloadTooLargeError("danh sách tối đa 10 MB", details={"max": MAX_LIST_BYTES})
        if not data:
            raise DomainError("file rỗng", details={"field": "file"})
        accepted = accepted_content_type(content_type, data[:16])
        new = NewProposalList(
            id=self.ids.new_uuid(),
            filename=clean_filename(filename),
            content_type=accepted,
            sha256=hashlib.sha256(data).hexdigest(),
            content=data,
        )
        await self.lists.add(
            context,
            new,
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=LIST_UPLOADED,
                resource_type=_RESOURCE,
                resource_id=str(new.id),
                occurred_at=self.clock.now(),
                details={"sha256": new.sha256, "size_bytes": len(data)},
            ),
        )
        stored = await self.lists.get(context, new.id)
        assert stored is not None
        return stored


# ------------------------------------------------------------------- read --


class ReadOutcome(StrEnum):
    READ = "read"
    REFUSED_BEFORE_READING = "refused_before_reading"
    DEFERRED = "deferred"
    ALREADY = "already"


@dataclass(slots=True)
class ReadCount:
    by_outcome: dict[str, int] = field(default_factory=dict)

    def count(self, outcome: str) -> None:
        self.by_outcome[outcome] = self.by_outcome.get(outcome, 0) + 1


@dataclass(frozen=True)
class ReadProposalLists:
    """The lane `supply_chain_proposal_lists` (module docstring)."""

    queue: ProposalListQueuePort
    lists: ProposalListRepositoryPort
    text: DocumentTextPort
    gateway: ModelGateway
    categories: CategoryListPort
    taken: TakenCodesPort
    plans: TenantPlanPort
    notifier: ReviewNotifierPort
    ids: IdGenerator
    clock: UtcClock
    model_profile: str | None = None
    batch_size: int = 5

    async def run_once(self) -> ReadCount:
        count = ReadCount()
        for item in await self.queue.awaiting(PROPOSAL_LIST_SPEC.prompt_ref, self.batch_size):
            count.count(await self.read(item))
        return count

    async def read(self, item: QueuedList) -> str:
        context = lane_context(item.tenant_id, item.workspace_id)
        found = await self.lists.get(context, item.list_id)
        if found is None or (found.tenant_id, found.workspace_id) != (
            item.tenant_id,
            item.workspace_id,
        ):
            logger.warning("proposal list refused: not in its queued workspace")
            return ReadOutcome.REFUSED_BEFORE_READING
        plan = await self.plans.plan_of(item.tenant_id)
        if plan is None:
            return ReadOutcome.DEFERRED
        data = await self.lists.content(context, found.id)
        if data is None or hashlib.sha256(data).hexdigest() != found.sha256:
            return await self._record(context, found, ExtractionStatus.FAILED, error="content")
        text = ""
        if self.text.supports(found.content_type):
            try:
                text = (await self.text.text_of(data, found.content_type, found.filename)).text
            except Exception:
                logger.warning("proposal list: the file could not be read", exc_info=True)
        if is_unreadable(text):
            return await self._record(
                context, found, ExtractionStatus.UNREADABLE, error="no readable text"
            )
        redacted = redact_identifiers(text)
        categories = await self.categories.categories(context)
        run_id = self.ids.new_uuid()
        try:
            reading = await self.gateway.generate_structured(
                ModelRequest(
                    task="structured_extraction",
                    prompt_id=PROPOSAL_LIST_SPEC.prompt_id,
                    prompt_version=PROPOSAL_LIST_SPEC.prompt_version,
                    variables={
                        PROPOSAL_LIST_VARIABLE: json.dumps(
                            {
                                "categories": [{"key": k, "label": v} for k, v in categories],
                                "text": redacted.text,
                            },
                            ensure_ascii=False,
                        )
                    },
                    model_profile=self.model_profile,
                    route_kind="structured_extraction",
                ),
                ProposalListReading,
                run_context=RunContext(
                    run_id=run_id,
                    tenant_id=item.tenant_id,
                    workspace_id=item.workspace_id,
                    actor_id=context.principal_id,
                    worker_id=PROPOSAL_LISTS_WORKER_ID,
                    worker_version=PROPOSAL_LISTS_WORKER_VERSION,
                    channel="worker",
                    plan_id=plan,
                    roles=frozenset(),
                    scopes=frozenset(),
                    trace_id=str(run_id),
                    subject_ref=f"proposal_list:{found.id}",
                ),
            )
        except QuotaExceededError:
            return ReadOutcome.DEFERRED
        except ModelOutputInvalidError:
            return await self._record(
                context, found, ExtractionStatus.REFUSED, redactions=redacted.count, error="schema"
            )
        except (InfrastructureError, BudgetExceededError) as exc:
            return await self._record(
                context,
                found,
                ExtractionStatus.FAILED,
                redactions=redacted.count,
                error=type(exc).__name__,
            )
        rows = ground_rows(reading, redacted.text, [k for k, _ in categories])
        proposal, item_codes, names = wanted_codes(rows)
        taken = await self.taken.taken(
            context, proposal_codes=proposal, item_codes=item_codes, product_names=names
        )
        reviewed = with_findings(rows, taken)
        return await self._record(
            context,
            found,
            ExtractionStatus.EXTRACTED,
            rows=tuple(r.as_json() for r in reviewed),
            redactions=redacted.count,
        )

    async def _record(
        self,
        context: AccessContext,
        found: ProposalList,
        status: ExtractionStatus,
        *,
        rows: tuple[Mapping[str, Any], ...] = (),
        redactions: int = 0,
        error: str | None = None,
    ) -> str:
        reading = NewListReading(
            id=self.ids.new_uuid(),
            list_id=found.id,
            status=status,
            prompt_id=PROPOSAL_LIST_SPEC.prompt_id,
            prompt_version=PROPOSAL_LIST_SPEC.prompt_version,
            model_profile=self.model_profile,
            rows=rows,
            redactions=redactions,
            error=error,
        )
        audit = lane_audit_event(
            lane=PROPOSAL_LISTS_LANE,
            event_id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            action=LIST_READ,
            resource_type=_RESOURCE,
            resource_id=str(found.id),
            occurred_at=self.clock.now(),
            details={"status": status.value, "rows": len(rows), "redactions": redactions},
        )
        try:
            await self.lists.add_reading(context, reading, audit=audit)
        except ConflictError:
            return ReadOutcome.ALREADY
        try:
            await self.notifier.deliver(
                context,
                recipients=[found.uploaded_by],
                source_key=f"supply_chain.proposal_list:{found.id}:{reading.prompt_version}",
                title="AI đã đọc danh sách sản phẩm đề xuất",
                body=(
                    f"{len(rows)} dòng chờ bạn kiểm và đề xuất từng dòng."
                    if status is ExtractionStatus.EXTRACTED
                    else "Máy không đọc được file này; hãy nhập tay hoặc tải bản PDF có chữ."
                ),
                link=f"/supply-chain/proposal-lists/{found.id}",
            )
        except Exception:
            logger.exception("proposal list read but its notification failed")
        return ReadOutcome.READ


# --------------------------------------------------------------- decisions --


@dataclass(frozen=True, slots=True)
class ProposalListView:
    proposal_list: ProposalList
    reading: ListReading | None
    decisions: Mapping[int, RowDecisionRecord]


async def _require_list(
    lists: ProposalListRepositoryPort, context: AccessContext, list_id: uuid.UUID
) -> ProposalList:
    found = await lists.get(context, list_id)
    if found is None or found.workspace_id != context.workspace_id:
        raise NotFoundError("proposal list not found", details={"list_id": str(list_id)})
    return found


@dataclass(frozen=True)
class GetProposalList:
    lists: ProposalListRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, list_id: uuid.UUID) -> ProposalListView:
        await self.authz.require(context=context, action=PRODUCT_CASE_READ, resource_type=_RESOURCE)
        found = await _require_list(self.lists, context, list_id)
        return ProposalListView(
            proposal_list=found,
            reading=await self.lists.latest_reading(context, list_id),
            decisions={d.row_index: d for d in await self.lists.decisions(context, list_id)},
        )


@dataclass(frozen=True)
class ListProposalLists:
    lists: ProposalListRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> list[ProposalList]:
        await self.authz.require(context=context, action=PRODUCT_CASE_READ, resource_type=_RESOURCE)
        return await self.lists.recent(context, 50)


async def _open_row(
    lists: ProposalListRepositoryPort, context: AccessContext, list_id: uuid.UUID, index: int
) -> None:
    await _require_list(lists, context, list_id)
    reading = await lists.latest_reading(context, list_id)
    if (
        reading is None
        or reading.status is not ExtractionStatus.EXTRACTED
        or not 0 <= index < len(reading.rows)
    ):
        raise NotFoundError("no such row", details={"list_id": str(list_id), "row": index})
    if any(d.row_index == index for d in await lists.decisions(context, list_id)):
        raise ConflictError("dòng này đã được xử lý", details={"row": index})


def _decision_audit(
    context: AccessContext, ids: IdGenerator, clock: UtcClock, decision: NewRowDecision
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=ROW_DECIDED,
        resource_type=_RESOURCE,
        resource_id=str(decision.list_id),
        occurred_at=clock.now(),
        details={
            "row": decision.row_index,
            "decision": decision.decision.value,
            "case_id": None
            if decision.product_dev_case_id is None
            else str(decision.product_dev_case_id),
        },
    )


@dataclass(frozen=True)
class ProposeFromList:
    lists: ProposalListRepositoryPort
    propose: ProposeProductCase
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        list_id: uuid.UUID,
        index: int,
        *,
        proposal_code: str,
        product_name: str,
        category: str,
    ) -> ProductDevelopmentCase:
        """The PIC's values, checked by `propose` like a form's; a code taken
        anywhere in the tenant is its refusal, and the row stays open."""
        for scope in sorted(await self.propose.propose_scopes(context)):
            await self.authz.require(context=context, action=scope, resource_type=_RESOURCE)
        await _open_row(self.lists, context, list_id, index)
        case = await self.propose.handle(
            context,
            proposal_code=proposal_code,
            product_name=product_name,
            category=category,
            origin=ProposalOrigin(channel="proposal_list", chat_ref=f"{list_id}#{index}"),
        )
        decision = NewRowDecision(
            id=self.ids.new_uuid(),
            list_id=list_id,
            row_index=index,
            decision=RowDecision.PROPOSED,
            product_dev_case_id=case.id.value,
            reason=None,
        )
        await self.lists.decide(
            context, decision, audit=_decision_audit(context, self.ids, self.clock, decision)
        )
        return case


@dataclass(frozen=True)
class DropFromList:
    lists: ProposalListRepositoryPort
    propose: ProposeProductCase
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, list_id: uuid.UUID, index: int, *, reason: str | None
    ) -> None:
        for scope in sorted(await self.propose.propose_scopes(context)):
            await self.authz.require(context=context, action=scope, resource_type=_RESOURCE)
        await _open_row(self.lists, context, list_id, index)
        text = (reason or "").strip() or None
        if text is not None and len(text) > _REASON_MAX:
            raise DomainError(f"lý do tối đa {_REASON_MAX} ký tự", details={"field": "reason"})
        decision = NewRowDecision(
            id=self.ids.new_uuid(),
            list_id=list_id,
            row_index=index,
            decision=RowDecision.DROPPED,
            product_dev_case_id=None,
            reason=text,
        )
        await self.lists.decide(
            context, decision, audit=_decision_audit(context, self.ids, self.clock, decision)
        )


__all__ = [
    "MAX_LIST_BYTES",
    "PROPOSAL_LISTS_LANE",
    "PROPOSAL_LIST_VARIABLE",
    "DropFromList",
    "GetProposalList",
    "ListProposalLists",
    "ListReading",
    "NewListReading",
    "NewProposalList",
    "NewRowDecision",
    "ProposalList",
    "ProposalListView",
    "ProposeFromList",
    "QueuedList",
    "ReadOutcome",
    "ReadProposalLists",
    "RowDecision",
    "RowDecisionRecord",
    "TenantCategories",
    "UploadProposalList",
    "lane_context",
]
