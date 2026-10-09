"""SQL persistence for document extractions (e21dc10d13b5).

`SqlDocumentExtractionRepository.add` runs under `tenant_session` (tenant AND
workspace bound; the table is narrowed by both, and its composite FK ties the
row to its own document's tenant, workspace, type and hash). The audit event
commits with the row. A second reading of one document under one prompt
version meets `uq_document_extractions_tenant_id_document_id_prompt` and comes
back as `ConflictError`.

`SqlExtractionQueue` asks `supply_chain.documents_awaiting_extraction`, the one
cross-tenant read this lane makes: ids only.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.adapters.persistence import tables
from dw_supply_chain.application.document_extraction import NewExtraction, QueuedDocument
from dw_supply_chain.application.step_preparation import StoredReading
from dw_supply_chain.domain.extraction import ExtractionStatus

_e = tables.document_extractions
UNIQUE_READING = "uq_document_extractions_tenant_id_document_id_prompt"


@dataclass(frozen=True)
class SqlDocumentExtractionRepository:
    """Implements `DocumentExtractionRepositoryPort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def add(
        self, context: AccessContext, extraction: NewExtraction, *, audit: AuditEvent
    ) -> None:
        try:
            async with tenant_session(
                self.session_factory, TenantScope.from_access_context(context)
            ) as session:
                await session.execute(
                    sa.insert(_e).values(
                        id=extraction.id,
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        document_id=extraction.document_id,
                        doc_type=extraction.doc_type.value,
                        sha256=extraction.sha256,
                        prompt_id=extraction.prompt_id,
                        prompt_version=extraction.prompt_version,
                        model_profile=extraction.model_profile,
                        status=extraction.status.value,
                        text=extraction.text,
                        fields=extraction.fields,
                        gaps=extraction.gaps,
                        redactions=extraction.redactions,
                        error=extraction.error,
                    )
                )
                await SqlAuditRepository(session).append(audit)
        except IntegrityError as exc:
            if UNIQUE_READING in str(exc.orig):
                raise ConflictError(
                    "this document was already read under this prompt version",
                    details={"document_id": str(extraction.document_id)},
                ) from exc
            raise


@dataclass(frozen=True)
class SqlExtractionQueue:
    """Implements `ExtractionQueuePort`."""

    session_factory: async_sessionmaker[AsyncSession]

    async def awaiting(self, targets: Mapping[str, str], limit: int) -> list[QueuedDocument]:
        async with self.session_factory() as session, session.begin():
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT tenant_id, workspace_id, document_id FROM"
                        " supply_chain.documents_awaiting_extraction(CAST(:t AS jsonb), :n)"
                    ),
                    {"t": json.dumps(dict(targets)), "n": limit},
                )
            ).all()
        return [
            QueuedDocument(
                tenant_id=r.tenant_id, workspace_id=r.workspace_id, document_id=r.document_id
            )
            for r in rows
        ]


@dataclass(frozen=True)
class SqlExtractionReadings:
    """Implements `ExtractionReadingsPort`: the readings of some documents, under
    the caller's tenant and workspace (RLS, and named in the statement)."""

    session_factory: async_sessionmaker[AsyncSession]

    async def readings(
        self, context: AccessContext, document_ids: Sequence[uuid.UUID]
    ) -> list[StoredReading]:
        if not document_ids:
            return []
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(_e).where(
                        _e.c.tenant_id == context.tenant_id,
                        _e.c.workspace_id == context.workspace_id,
                        _e.c.document_id.in_(list(document_ids)),
                    )
                )
            ).all()
        return [
            StoredReading(
                id=r.id,
                document_id=r.document_id,
                sha256=r.sha256,
                prompt_id=r.prompt_id,
                prompt_version=r.prompt_version,
                status=ExtractionStatus(r.status),
                fields=dict(r.fields),
                gaps=list(r.gaps),
            )
            for r in rows
        ]
