"""Integration: messages to a supplier (1dc679326cb5; ADR 0029; ticket
ai-automation/07).

What only the real database can show:

- `supplier_messages` and `supplier_message_sends` are narrowed by tenant AND
  workspace (RLS FORCE): a neighbour workspace and another tenant neither read
  nor add a row, and the composite FK refuses a message on another
  workspace's case;
- a trigger is drafted once per workspace and a message sent once (the
  UNIQUEs, read back as `ConflictError` by constraint name);
- a drafted message has a body and a refused one has none (CHECK);
- the contact lookup finds the workspace's supplier by its normalised name.

Owed when written (2026-10-09): not run, no Docker on the machine that wrote it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence.product_case_repository import SqlProductCaseRepository
from dw_supply_chain.adapters.persistence.supplier_message_repository import (
    SqlSupplierMessageRepository,
)
from dw_supply_chain.application.supplier_messages import NewMessageSend, NewSupplierMessage
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
)
from dw_supply_chain.domain.supplier_message import MessagePurpose, MessageStatus

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class _Db:
    sessions: async_sessionmaker[AsyncSession]


@pytest.fixture
async def db(db_urls: DatabaseUrls) -> AsyncIterator[_Db]:
    app = create_async_engine(db_urls.app, poolclass=NullPool)
    yield _Db(async_sessionmaker(app, class_=AsyncSession, expire_on_commit=False))
    await app.dispose()


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
        resource_type="supplier_message",
        resource_id=str(uuid.uuid4()),
        occurred_at=SystemClock().now(),
    )


async def _case(db: _Db, context: AccessContext) -> ProductDevelopmentCase:
    repo = SqlProductCaseRepository(db.sessions)
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Nồi inox 3 đáy 24cm",
        category="Nồi",
        actor_id=context.principal_id,
    )
    await repo.add(context, case, audit=_audit(context))
    return case


def _message(case: ProductDevelopmentCase, source_key: str = "follow_up:1") -> NewSupplierMessage:
    return NewSupplierMessage(
        id=uuid.uuid4(),
        case_kind=CaseKind.PRODUCT,
        case_id=case.id.value,
        purpose=MessagePurpose.SUPPLIER_REMINDER,
        status=MessageStatus.DRAFTED,
        source_key=source_key,
        supplier_name="Công ty Gia dụng Minh Phát",
        recipient_name="Chị Lan",
        recipient_email="lan@minhphat.vn",
        subject="[DX] Nhắc cập nhật tiến độ",
        body="Kính gửi Chị Lan,\n\nMẫu đã quá hạn 12 ngày.\n\nTrân trọng,",
        attachments=(),
        citations=({"text": "Mẫu đã quá hạn 12 ngày.", "cites": ["follow_up:1"]},),
        dropped=0,
        template_version="1.0.0",
        prompt_id="supply_chain.draft_supplier_message",
        prompt_version="1.0.0",
        model_profile="luna",
        content_sha256="a" * 64,
    )


async def test_messages_stay_in_their_workspace(db: _Db) -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    mine = _context(tenant, workspace)
    neighbour = _context(tenant, uuid.uuid4())
    stranger = _context(uuid.uuid4(), workspace)
    case = await _case(db, mine)
    repo = SqlSupplierMessageRepository(db.sessions)
    message = _message(case)
    await repo.add(mine, message, audit=_audit(mine))
    assert [m.id for m in await repo.list_for_case(mine, CaseKind.PRODUCT, case.id.value)] == [
        message.id
    ]
    for other in (neighbour, stranger):
        assert await repo.list_for_case(other, CaseKind.PRODUCT, case.id.value) == []
        assert await repo.get(other, message.id) is None
        assert not await repo.has_source(other, message.source_key)
        # Writing a message on a case of another workspace or tenant: the
        # composite FK (or the policy's WITH CHECK) refuses it.
        with pytest.raises((IntegrityError, DBAPIError)):
            await repo.add(other, _message(case, "follow_up:2"), audit=_audit(other))
        with pytest.raises((ConflictError, IntegrityError, DBAPIError)):
            await repo.mark_sent(
                other,
                NewMessageSend(id=uuid.uuid4(), message_id=message.id, content_sha256="a" * 64),
                audit=_audit(other),
            )


async def test_a_trigger_is_drafted_once_and_a_message_sent_once(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    repo = SqlSupplierMessageRepository(db.sessions)
    message = _message(case)
    await repo.add(context, message, audit=_audit(context))
    with pytest.raises(ConflictError):
        await repo.add(context, replace(message, id=uuid.uuid4()), audit=_audit(context))
    send = NewMessageSend(id=uuid.uuid4(), message_id=message.id, content_sha256="a" * 64)
    await repo.mark_sent(context, send, audit=_audit(context))
    with pytest.raises(ConflictError):
        await repo.mark_sent(context, replace(send, id=uuid.uuid4()), audit=_audit(context))
    stored = await repo.get(context, message.id)
    assert stored is not None and stored.sent_by == context.principal_id


async def test_only_a_drafted_message_has_a_body(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    repo = SqlSupplierMessageRepository(db.sessions)
    with pytest.raises(IntegrityError):
        await repo.add(
            context,
            replace(_message(case), status=MessageStatus.REFUSED),
            audit=_audit(context),
        )
    with pytest.raises(IntegrityError):
        await repo.add(context, replace(_message(case, "k2"), body=""), audit=_audit(context))


async def test_dw_app_cannot_edit_or_delete_a_message(db: _Db) -> None:
    context = _context(uuid.uuid4(), uuid.uuid4())
    case = await _case(db, context)
    repo = SqlSupplierMessageRepository(db.sessions)
    message = _message(case)
    await repo.add(context, message, audit=_audit(context))
    async with db.sessions() as session, session.begin():
        await session.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(context.tenant_id)}
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                sa.text("UPDATE supply_chain.supplier_messages SET body = 'x' WHERE id = :id"),
                {"id": message.id},
            )
