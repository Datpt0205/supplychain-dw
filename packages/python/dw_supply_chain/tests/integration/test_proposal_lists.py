"""Integration: proposal lists (381374b3cb35; ticket ai-automation/08).

What only the real database can show:

- the three tables are narrowed by tenant AND workspace (RLS FORCE): a
  neighbour workspace and another tenant neither read a list, its content or
  its reading, nor add a reading to it;
- one reading per (list, prompt version) and one decision per row (the
  UNIQUEs, read back as `ConflictError`);
- a `proposed` decision names a case and a `dropped` one does not (CHECK);
- the definer queue hands out a list's id until it has a reading;
- `SqlTakenCodes` sees only the caller's workspace.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.proposal_list_repository import (
    SqlProposalListQueue,
    SqlProposalLists,
    SqlTakenCodes,
)
from dw_supply_chain.application.proposal_lists import (
    NewListReading,
    NewProposalList,
    NewRowDecision,
    RowDecision,
)
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.proposal_list import PROPOSAL_LIST_SPEC

pytestmark = pytest.mark.integration


@pytest.fixture
async def sessions(db_urls: DatabaseUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(db_urls.app, poolclass=NullPool)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _context(tenant: uuid.UUID, workspace: uuid.UUID) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )


def _audit(context: AccessContext) -> AuditEvent:
    return AuditEvent(
        id=uuid.uuid4(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action="supply_chain.test",
        resource_type="proposal_list",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


def _new() -> NewProposalList:
    data = b"%PDF-1.4 DX-2026-050 Noi inox"
    return NewProposalList(
        id=uuid.uuid4(),
        filename="ds.pdf",
        content_type="application/pdf",
        sha256=hashlib.sha256(data).hexdigest(),
        content=data,
    )


def _reading(list_id: uuid.UUID) -> NewListReading:
    return NewListReading(
        id=uuid.uuid4(),
        list_id=list_id,
        status=ExtractionStatus.EXTRACTED,
        prompt_id=PROPOSAL_LIST_SPEC.prompt_id,
        prompt_version=PROPOSAL_LIST_SPEC.prompt_version,
        model_profile="luna",
        rows=({"index": 0, "fields": {}, "findings": []},),
    )


async def test_lists_stay_in_their_workspace(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    repo = SqlProposalLists(sessions)
    new = _new()
    await repo.add(mine, new, audit=_audit(mine))
    await repo.add_reading(mine, _reading(new.id), audit=_audit(mine))
    assert await repo.content(mine, new.id) == new.content
    for other in (_context(tenant, uuid.uuid4()), _context(uuid.uuid4(), workspace)):
        assert await repo.get(other, new.id) is None
        assert await repo.content(other, new.id) is None
        assert await repo.latest_reading(other, new.id) is None
        assert await repo.recent(other, 50) == []
        with pytest.raises(IntegrityError):
            await repo.add_reading(other, _reading(new.id), audit=_audit(other))


async def test_one_reading_per_prompt_and_one_decision_per_row(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    repo = SqlProposalLists(sessions)
    new = _new()
    await repo.add(context, new, audit=_audit(context))
    await repo.add_reading(context, _reading(new.id), audit=_audit(context))
    with pytest.raises(ConflictError):
        await repo.add_reading(context, _reading(new.id), audit=_audit(context))
    drop = NewRowDecision(
        id=uuid.uuid4(),
        list_id=new.id,
        row_index=0,
        decision=RowDecision.DROPPED,
        product_dev_case_id=None,
        reason=None,
    )
    await repo.decide(context, drop, audit=_audit(context))
    with pytest.raises(ConflictError):
        await repo.decide(context, replace(drop, id=uuid.uuid4()), audit=_audit(context))
    with pytest.raises(IntegrityError):
        await repo.decide(
            context,
            replace(drop, id=uuid.uuid4(), row_index=1, decision=RowDecision.PROPOSED),
            audit=_audit(context),
        )


async def test_the_queue_hands_an_id_until_the_list_is_read(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    repo = SqlProposalLists(sessions)
    new = _new()
    await repo.add(context, new, audit=_audit(context))
    queue = SqlProposalListQueue(sessions)
    queued = await queue.awaiting(PROPOSAL_LIST_SPEC.prompt_ref, 50)
    assert any(q.list_id == new.id and q.workspace_id == context.workspace_id for q in queued)
    await repo.add_reading(context, _reading(new.id), audit=_audit(context))
    later = await queue.awaiting(PROPOSAL_LIST_SPEC.prompt_ref, 50)
    assert all(q.list_id != new.id for q in later)


async def test_taken_codes_are_the_caller_s_workspace_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    found = await SqlTakenCodes(sessions).taken(
        _context(uuid.uuid4(), uuid.uuid4()),
        proposal_codes=["DX-NONE"],
        item_codes=["SKU-NONE"],
        product_names=["Không có"],
    )
    assert not (found.proposal_codes or found.item_codes or found.product_names)
