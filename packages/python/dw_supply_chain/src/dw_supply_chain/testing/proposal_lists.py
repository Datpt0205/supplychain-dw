"""An in-memory world for proposal lists (ticket ai-automation/08): its unit
tests and the `supply_chain.proposal_list` eval grader run the REAL
`ReadProposalLists`, `ProposeFromList` and `DropFromList` over it, with the
shipped prompt and skills rendered through the real registry.

Every store keeps its port's promise: a reader sees only its own tenant AND
workspace (RLS), a list is read once per prompt version and a row decided once
(the UNIQUEs). `leaky=True` stands in for an adapter that forgot the
workspace, so the lane's own check can be exercised.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dw_kernel.errors import ConflictError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.proposal_lists import (
    ListReading,
    NewListReading,
    NewProposalList,
    NewRowDecision,
    ProposalList,
    QueuedList,
    ReadProposalLists,
    RowDecisionRecord,
)
from dw_supply_chain.domain.proposal_list import TakenCodes
from dw_supply_chain.testing.extraction import PDF, PlainTextReader
from dw_supply_chain.testing.step_preparation import NOW
from dw_supply_chain.testing.supplier_messages import RecordingNotifier, StaticPlans

CATEGORIES = [("noi", "Nồi"), ("chao", "Chảo")]


def _sees(context: AccessContext, tenant: uuid.UUID, workspace: uuid.UUID) -> bool:
    return (tenant, workspace) == (context.tenant_id, context.workspace_id)


@dataclass
class InMemoryLists:
    lists: list[tuple[ProposalList, bytes]] = field(default_factory=list)
    readings: list[tuple[uuid.UUID, uuid.UUID, ListReading]] = field(default_factory=list)
    decided: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, RowDecisionRecord]] = field(
        default_factory=list
    )
    audits: list[AuditEvent] = field(default_factory=list)
    leaky: bool = False

    def _seen(self, context: AccessContext, found: ProposalList) -> bool:
        return self.leaky or _sees(context, found.tenant_id, found.workspace_id)

    async def add(self, context: AccessContext, new: NewProposalList, *, audit: AuditEvent) -> None:
        self.lists.append(
            (
                ProposalList(
                    id=new.id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    filename=new.filename,
                    content_type=new.content_type,
                    size_bytes=len(new.content),
                    sha256=new.sha256,
                    uploaded_by=context.principal_id,
                    created_at=NOW,
                ),
                new.content,
            )
        )
        self.audits.append(audit)

    async def get(self, context: AccessContext, list_id: uuid.UUID) -> ProposalList | None:
        return next((f for f, _ in self.lists if f.id == list_id and self._seen(context, f)), None)

    async def content(self, context: AccessContext, list_id: uuid.UUID) -> bytes | None:
        return next((d for f, d in self.lists if f.id == list_id and self._seen(context, f)), None)

    async def recent(self, context: AccessContext, limit: int) -> list[ProposalList]:
        return [f for f, _ in reversed(self.lists) if self._seen(context, f)][:limit]

    async def latest_reading(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> ListReading | None:
        mine = [
            r
            for t, w, r in self.readings
            if r.list_id == list_id
            and (self.leaky or (t, w) == (context.tenant_id, context.workspace_id))
        ]
        return mine[-1] if mine else None

    async def add_reading(
        self, context: AccessContext, reading: NewListReading, *, audit: AuditEvent
    ) -> None:
        if any(
            r.list_id == reading.list_id and r.prompt_version == reading.prompt_version
            for _, _, r in self.readings
        ):
            raise ConflictError("already read")
        self.readings.append(
            (
                context.tenant_id,
                context.workspace_id,
                ListReading(
                    id=reading.id,
                    list_id=reading.list_id,
                    status=reading.status,
                    prompt_id=reading.prompt_id,
                    prompt_version=reading.prompt_version,
                    rows=reading.rows,
                    error=reading.error,
                    created_at=NOW,
                ),
            )
        )
        self.audits.append(audit)

    async def decisions(
        self, context: AccessContext, list_id: uuid.UUID
    ) -> list[RowDecisionRecord]:
        return [
            d
            for t, w, lid, d in self.decided
            if lid == list_id
            and (self.leaky or (t, w) == (context.tenant_id, context.workspace_id))
        ]

    async def decide(
        self, context: AccessContext, decision: NewRowDecision, *, audit: AuditEvent
    ) -> None:
        if any(
            lid == decision.list_id and d.row_index == decision.row_index
            for _, _, lid, d in self.decided
        ):
            raise ConflictError("row decided")
        self.decided.append(
            (
                context.tenant_id,
                context.workspace_id,
                decision.list_id,
                RowDecisionRecord(
                    row_index=decision.row_index,
                    decision=decision.decision,
                    product_dev_case_id=decision.product_dev_case_id,
                    reason=decision.reason,
                    decided_by=context.principal_id,
                    decided_at=NOW,
                ),
            )
        )
        self.audits.append(audit)


@dataclass
class ListedQueue:
    """The definer queue: queued ids whose list has no reading under the
    prompt version yet (whatever workspace the id claims)."""

    lists: InMemoryLists
    items: list[QueuedList] = field(default_factory=list)

    async def awaiting(self, prompt_ref: str, limit: int) -> list[QueuedList]:
        read = {
            r.list_id
            for _, _, r in self.lists.readings
            if f"{r.prompt_id}@{r.prompt_version}" == prompt_ref
        }
        return [i for i in self.items if i.list_id not in read][:limit]


@dataclass
class InMemoryTaken:
    """The workspace's codes and names, by (tenant, workspace)."""

    held: dict[tuple[uuid.UUID, uuid.UUID], TakenCodes] = field(default_factory=dict)
    asked: list[tuple[Sequence[str], Sequence[str], Sequence[str]]] = field(default_factory=list)

    async def taken(
        self,
        context: AccessContext,
        *,
        proposal_codes: Sequence[str],
        item_codes: Sequence[str],
        product_names: Sequence[str],
    ) -> TakenCodes:
        self.asked.append((proposal_codes, item_codes, product_names))
        held = self.held.get((context.tenant_id, context.workspace_id), TakenCodes())
        return TakenCodes(
            proposal_codes=frozenset(c for c in proposal_codes if c in held.proposal_codes),
            item_codes=frozenset(c for c in item_codes if c in held.item_codes),
            product_names=frozenset(n for n in product_names if n in held.product_names),
        )


@dataclass
class StaticCategories:
    items: list[tuple[str, str]] = field(default_factory=lambda: list(CATEGORIES))

    async def categories(self, context: AccessContext) -> list[tuple[str, str]]:
        return list(self.items)


@dataclass
class ListWorld:
    gateway: Any
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    lists: InMemoryLists = field(default_factory=InMemoryLists)
    queue: ListedQueue = field(init=False)
    taken: InMemoryTaken = field(default_factory=InMemoryTaken)
    notifier: RecordingNotifier = field(default_factory=RecordingNotifier)
    plans: StaticPlans = field(default_factory=StaticPlans)
    model_profile: str | None = None

    def __post_init__(self) -> None:
        self.plans.plans[self.tenant_id] = "professional"
        self.queue = ListedQueue(self.lists)

    def context(
        self,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> AccessContext:
        return AccessContext(
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            principal_id=uuid.uuid4(),
            roles=frozenset(),
            scopes=scopes,
            plan_id="professional",
        )

    def add_list(
        self,
        text: str,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
        content_type: str = PDF,
    ) -> ProposalList:
        data = text.encode("utf-8")
        found = ProposalList(
            id=uuid.uuid4(),
            tenant_id=tenant or self.tenant_id,
            workspace_id=workspace or self.workspace_id,
            filename="danh-sach.pdf",
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            uploaded_by=uuid.uuid4(),
            created_at=NOW,
        )
        self.lists.lists.append((found, data))
        return found

    def queue_as(
        self,
        found: ProposalList,
        *,
        tenant: uuid.UUID | None = None,
        workspace: uuid.UUID | None = None,
    ) -> None:
        self.queue.items.append(
            QueuedList(
                tenant_id=tenant or self.tenant_id,
                workspace_id=workspace or self.workspace_id,
                list_id=found.id,
            )
        )

    def lane(self) -> ReadProposalLists:
        return ReadProposalLists(
            queue=self.queue,
            lists=self.lists,
            text=PlainTextReader(),
            gateway=self.gateway,
            categories=StaticCategories(),
            taken=self.taken,
            plans=self.plans,
            notifier=self.notifier,
            ids=Uuid4Generator(),
            clock=FixedClock(NOW),
            model_profile=self.model_profile,
        )


__all__ = ["CATEGORIES", "InMemoryLists", "InMemoryTaken", "ListWorld"]
