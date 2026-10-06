"""Chat proposal drafts (zalo-channel ticket 04): one person's open proposal in
one workspace, through one channel.

Every statement runs under the caller's tenant and workspace (RLS) and names
them again, and names the person as `context.principal_id`: there is no
parameter through which a caller could reach somebody else's draft.

`SqlProposalDraftRetention` is the exception, and the only one: the worker's
retention lane deletes expired drafts of every tenant in one statement under
`app.worker_drain`, set for its own transaction (migration d4048e50d4a3).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.domain.product_proposal import (
    DRAFT_SCHEMA_VERSION,
    ProposalDraft,
    ProposalDraftChangedError,
    ProposalField,
)

_d = tables.proposal_drafts
_SET_DRAIN = sa.text("SELECT set_config('app.worker_drain', 'on', true)")


def _mine(context: AccessContext, channel: str) -> tuple[sa.ColumnElement[bool], ...]:
    return (
        _d.c.tenant_id == context.tenant_id,
        _d.c.workspace_id == context.workspace_id,
        _d.c.user_id == context.principal_id,
        _d.c.channel == channel,
    )


def _draft(row: Row[tuple[object, ...]]) -> ProposalDraft:
    stored = row.draft
    current = stored.get("schema_version") == DRAFT_SCHEMA_VERSION
    known = {field.value for field in ProposalField}
    fields = {
        ProposalField(name): value
        for name, value in ((stored.get("fields") or {}).items() if current else ())
        if name in known and isinstance(value, str)
    }
    return ProposalDraft(
        id=row.id,
        tenant_id=row.tenant_id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        channel=row.channel,
        fields=fields,
        draft_version=row.draft_version,
        summarized_version=row.summarized_version,
        # A draft of another schema version is not this one: read as expired.
        expires_at=row.expires_at if current else datetime.min.replace(tzinfo=UTC),
    )


def _stored(fields: Mapping[ProposalField, str]) -> dict[str, object]:
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "fields": {field.value: value for field, value in fields.items()},
    }


@dataclass(frozen=True)
class SqlProposalDraftRepository:
    """Implements `ProposalDraftRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def open_draft(self, context: AccessContext, channel: str) -> ProposalDraft | None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            row = (await session.execute(sa.select(_d).where(*_mine(context, channel)))).first()
        return None if row is None else _draft(row)

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
        values = {
            "draft": _stored(fields),
            "draft_version": draft_version,
            "summarized_version": summarized_version,
            "expires_at": expires_at,
        }
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            if previous_version is None:
                inserted = (
                    pg_insert(_d)
                    .values(
                        id=uuid.uuid4(),
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        user_id=context.principal_id,
                        channel=channel,
                        **values,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_proposal_drafts_tenant_id_workspace_id_user_id_channel"
                    )
                    .returning(*_d.c)
                )
                row = (await session.execute(inserted)).first()
            else:
                updated = (
                    sa.update(_d)
                    .where(*_mine(context, channel), _d.c.draft_version == previous_version)
                    .values(**values)
                    .returning(*_d.c)
                )
                row = (await session.execute(updated)).first()
        if row is None:
            raise ProposalDraftChangedError(
                "bản nháp đề xuất đã bị đổi bởi một tin khác",
                details={"channel": channel},
            )
        return _draft(row)

    async def discard(self, context: AccessContext, channel: str) -> bool:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            result = await session.execute(sa.delete(_d).where(*_mine(context, channel)))
        assert isinstance(result, CursorResult)
        return bool(result.rowcount)


@dataclass(frozen=True)
class SqlProposalDraftRetention:
    """Implements `dw_worker.consumers.retention.RetentionPrunePort`: deletes
    every draft past its `expires_at` (`DRAFT_TTL` after its last message),
    whatever tenant it is in. A technical bound on a half-finished
    conversation, not a legal term, so it is not in `retention@*.yaml`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def prune(self) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(_SET_DRAIN)
            await session.execute(sa.delete(_d).where(_d.c.expires_at <= sa.func.now()))
