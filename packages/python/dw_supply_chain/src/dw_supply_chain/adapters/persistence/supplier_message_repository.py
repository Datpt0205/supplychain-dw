"""SQL persistence for messages to a supplier (1dc679326cb5; ADR 0029).

Both tables run under `tenant_session` (tenant AND workspace bound; both are
narrowed by both) and name the tenant and workspace in every statement as a
second layer. Drafting once per trigger and sending once per message are the
database's: the UNIQUEs turn a race into a `ConflictError` by constraint name.
Audit events commit with their row. The supplier's contact is looked up by the
directory's own normalised name (`suppliers.py`), in the caller's workspace.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.supplier_messages import (
    NewMessageSend,
    NewSupplierMessage,
    SupplierMessage,
)
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.commercial import SupplierContact
from dw_supply_chain.domain.supplier_message import MessagePurpose, MessageStatus

_m = tables.supplier_messages
_x = tables.supplier_message_sends
_s = tables.suppliers
_c = tables.supplier_contacts
_CASE_COLUMN = {CaseKind.PO: _m.c.po_case_id, CaseKind.PRODUCT: _m.c.product_dev_case_id}
SOURCE_TAKEN = "uq_supplier_messages_tenant_id_workspace_id_source_key"
ALREADY_SENT = "uq_supplier_message_sends_tenant_id_message_id"


def _scope(context: AccessContext) -> TenantScope:
    return TenantScope.from_access_context(context)


def _mine(table: sa.Table, context: AccessContext) -> tuple[sa.ColumnElement[bool], ...]:
    return (table.c.tenant_id == context.tenant_id, table.c.workspace_id == context.workspace_id)


def _select(context: AccessContext) -> sa.Select[Any]:
    return (
        sa.select(_m, _x.c.sent_by, _x.c.sent_at)
        .select_from(
            _m.outerjoin(_x, sa.and_(_x.c.tenant_id == _m.c.tenant_id, _x.c.message_id == _m.c.id))
        )
        .where(*_mine(_m, context))
    )


def _message(row: Row[Any]) -> SupplierMessage:
    m = row._mapping
    kind = CaseKind.PO if m[_m.c.po_case_id] is not None else CaseKind.PRODUCT
    return SupplierMessage(
        id=m[_m.c.id],
        tenant_id=m[_m.c.tenant_id],
        workspace_id=m[_m.c.workspace_id],
        case_kind=kind,
        case_id=m[_CASE_COLUMN[kind]],
        purpose=MessagePurpose(m[_m.c.purpose]),
        status=MessageStatus(m[_m.c.status]),
        source_key=m[_m.c.source_key],
        supplier_name=m[_m.c.supplier_name],
        recipient_name=m[_m.c.recipient_name],
        recipient_email=m[_m.c.recipient_email],
        subject=m[_m.c.subject],
        body=m[_m.c.body],
        attachments=tuple(uuid.UUID(str(a)) for a in m[_m.c.attachments]),
        citations=tuple(m[_m.c.citations]),
        dropped=m[_m.c.dropped],
        template_version=m[_m.c.template_version],
        prompt_id=m[_m.c.prompt_id],
        prompt_version=m[_m.c.prompt_version],
        model_profile=m[_m.c.model_profile],
        content_sha256=m[_m.c.content_sha256],
        created_by=m[_m.c.created_by],
        created_at=m[_m.c.created_at],
        sent_by=m[_x.c.sent_by],
        sent_at=m[_x.c.sent_at],
    )


@dataclass(frozen=True)
class SqlSupplierMessageRepository:
    """Implements `SupplierMessageRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def has_source(self, context: AccessContext, source_key: str) -> bool:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            found = await session.scalar(
                sa.select(sa.literal(True)).where(
                    *_mine(_m, context), _m.c.source_key == source_key
                )
            )
        return bool(found)

    async def add(
        self, context: AccessContext, message: NewSupplierMessage, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await session.execute(
                    sa.insert(_m).values(
                        id=message.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        **{_CASE_COLUMN[message.case_kind].name: message.case_id},
                        purpose=message.purpose.value,
                        status=message.status.value,
                        source_key=message.source_key,
                        supplier_name=message.supplier_name,
                        recipient_name=message.recipient_name,
                        recipient_email=message.recipient_email,
                        subject=message.subject,
                        body=message.body,
                        attachments=[str(a) for a in message.attachments],
                        citations=[dict(c) for c in message.citations],
                        dropped=message.dropped,
                        template_version=message.template_version,
                        prompt_id=message.prompt_id,
                        prompt_version=message.prompt_version,
                        model_profile=message.model_profile,
                        content_sha256=message.content_sha256,
                        created_by=context.principal_id,
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if SOURCE_TAKEN in str(exc.orig):
                raise ConflictError(
                    "this trigger already has a message", details={"source_key": message.source_key}
                ) from exc
            raise

    async def get(self, context: AccessContext, message_id: uuid.UUID) -> SupplierMessage | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (await session.execute(_select(context).where(_m.c.id == message_id))).first()
        return None if row is None else _message(row)

    async def list_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[SupplierMessage]:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            rows = (
                await session.execute(
                    _select(context)
                    .where(_CASE_COLUMN[case_kind] == case_id)
                    .order_by(_m.c.created_at.desc(), _m.c.id.desc())
                    .limit(200)
                )
            ).all()
        return [_message(r) for r in rows]

    async def mark_sent(
        self, context: AccessContext, send: NewMessageSend, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(self.session_factory, _scope(context)) as session:
                await session.execute(
                    sa.insert(_x).values(
                        id=send.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        message_id=send.message_id,
                        content_sha256=send.content_sha256,
                        sent_by=context.principal_id,
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if ALREADY_SENT in str(exc.orig):
                raise ConflictError(
                    "thư này đã được đánh dấu đã gửi",
                    details={"message_id": str(send.message_id)},
                ) from exc
            raise


@dataclass(frozen=True)
class SqlSupplierContactLookup:
    """Implements `SupplierContactLookupPort`: the newest contact version of
    the workspace's supplier whose normalised name equals the case's."""

    session_factory: async_sessionmaker[AsyncSession]

    async def contact_for(
        self, context: AccessContext, supplier_name: str
    ) -> SupplierContact | None:
        async with tenant_session(self.session_factory, _scope(context)) as session:
            row = (
                await session.execute(
                    sa.select(_c)
                    .join(
                        _s,
                        sa.and_(
                            _s.c.tenant_id == _c.c.tenant_id,
                            _s.c.workspace_id == _c.c.workspace_id,
                            _s.c.id == _c.c.supplier_id,
                        ),
                    )
                    .where(
                        *_mine(_c, context),
                        _s.c.normalized_name
                        == sa.func.supply_chain.normalize_supplier_name(supplier_name),
                    )
                    .order_by(_c.c.version.desc())
                    .limit(1)
                )
            ).first()
        if row is None:
            return None
        m = row._mapping
        return SupplierContact(
            id=m[_c.c.id],
            supplier_id=m[_c.c.supplier_id],
            version=m[_c.c.version],
            name=m[_c.c.name],
            email=m[_c.c.email],
            phone=m[_c.c.phone],
            created_by=m[_c.c.created_by],
            created_at=m[_c.c.created_at],
        )
