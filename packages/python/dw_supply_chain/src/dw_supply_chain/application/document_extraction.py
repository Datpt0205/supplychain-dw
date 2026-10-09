"""The document extraction lane (ADR 0021 amended 2026-10-09; ticket
ai-automation/02).

A case document of a type `EXTRACTION_SPECS` reads becomes one
`document_extractions` row per prompt version: its text (redacted), the fields
its text proves, the gaps it does not, and a status. The lane decides nothing
about the case: a reading only fills a draft or a finding a person reviews.

The order, and what each step refuses:

1. **Which document.** The queue hands ids only (`tenant, workspace, document`).
   The document is read under THAT tenant's and workspace's RLS, as the lane
   (`system:supply_chain_document_extraction`); a row it cannot see, or one
   whose tenant or workspace is not the queue's, is refused before anything
   else, with no model call and no row written.
2. **Whose plan.** The tenant's plan, read by the platform; a tenant with none
   gets no call (fail closed). The call goes through the process's one-call
   gateway, so `DailyAllowance` refuses a spent day before it and the spend
   ledger records it.
3. **Which bytes.** The stored object must hash to the row's `sha256`;
   otherwise the reading is `failed` (the object is not the document).
4. **Text, in process.** PDF text layers, DOCX, XLSX and EML are read without a
   model (`DocumentTextPort`). An image, a scan without text, an MSG or an
   empty text is `unreadable`: nothing is guessed, and no file goes to a model
   before its identifiers could be masked.
5. **Redaction**, then **one structured call** with the text as the prompt's
   one untrusted variable (ADR 0010), then **grounding** in code.

Errors are recorded, not retried for ever: an invalid model output is
`refused`, a provider failure after the gateway's own retries is `failed`.
A spent allowance writes nothing: no call was made, and the next tick asks
again (each ask is a read, never a model call).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError, ModelRequest
from dw_kernel.errors import ConflictError, InfrastructureError, QuotaExceededError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent, lane_audit_event, system_actor
from dw_supply_chain.application.ports import TenantPlanPort
from dw_supply_chain.domain.case_document import CaseDocument, CaseDocumentId, DocumentType
from dw_supply_chain.domain.extraction import (
    EXTRACTION_SPECS,
    ExtractionSpec,
    ExtractionStatus,
    gaps_json,
    ground,
    is_unreadable,
    redact_identifiers,
)

logger = logging.getLogger(__name__)

EXTRACTION_LANE = "supply_chain_document_extraction"
EXTRACTION_WORKER_ID = "supply_chain.document_extraction"
EXTRACTION_WORKER_VERSION = "1.0.0"
DOCUMENT_EXTRACTED = "supply_chain.document.extracted"
# The prompt's one untrusted variable.
DOCUMENT_TEXT_VARIABLE = "document_text"


def lane_context(tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> AccessContext:
    """The lane, in one workspace: no role, no scope, its own actor."""
    return AccessContext(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        principal_id=system_actor(EXTRACTION_LANE).value,
        roles=frozenset(),
        scopes=frozenset(),
        plan_id="system",
    )


@dataclass(frozen=True, slots=True)
class QueuedDocument:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    document_id: uuid.UUID


class ExtractionQueuePort(Protocol):
    """Ids of documents of a target type with no reading under its prompt
    version, oldest first. `targets` maps a doc type to `prompt_id@version`."""

    async def awaiting(self, targets: Mapping[str, str], limit: int) -> list[QueuedDocument]: ...


class ExtractionDocumentsPort(Protocol):
    async def get(
        self, context: AccessContext, document_id: CaseDocumentId
    ) -> CaseDocument | None: ...


class DocumentBytesPort(Protocol):
    async def get(self, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class DocumentText:
    text: str
    warnings: tuple[str, ...] = ()


class DocumentTextPort(Protocol):
    """A file to text, without a model."""

    def supports(self, content_type: str) -> bool: ...

    async def text_of(self, data: bytes, content_type: str, filename: str) -> DocumentText: ...


@dataclass(frozen=True, slots=True)
class NewExtraction:
    id: uuid.UUID
    document_id: uuid.UUID
    doc_type: DocumentType
    sha256: str
    prompt_id: str
    prompt_version: str
    model_profile: str | None
    status: ExtractionStatus
    text: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    gaps: list[dict[str, str]] = field(default_factory=list)
    redactions: int = 0
    error: str | None = None


class DocumentExtractionRepositoryPort(Protocol):
    async def add(
        self, context: AccessContext, extraction: NewExtraction, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the document already has a reading
        under this prompt version (another tick got there first)."""
        ...


@dataclass(slots=True)
class ExtractionOutcome:
    by_status: dict[ExtractionStatus, int] = field(default_factory=dict)
    refused_before_reading: int = 0
    deferred: int = 0

    def count(self, status: ExtractionStatus) -> None:
        self.by_status[status] = self.by_status.get(status, 0) + 1


@dataclass(frozen=True)
class ExtractDocuments:
    queue: ExtractionQueuePort
    documents: ExtractionDocumentsPort
    storage: DocumentBytesPort
    text: DocumentTextPort
    gateway: ModelGateway
    extractions: DocumentExtractionRepositoryPort
    plans: TenantPlanPort
    ids: IdGenerator
    clock: UtcClock
    # The profile the call runs on: None is the deployment's own (`luna` for
    # Elmich, ADR 0025 point 10). Stored on each row as given.
    model_profile: str | None = None
    # A document type whose reading runs on another profile than the process's
    # (`supply_chain_model_routes`, ticket ai-automation/06): only a profile that
    # passed the model gate for that task gets here, checked when the policy
    # loads. Stored on each row as used.
    routes: Mapping[DocumentType, str] = field(default_factory=dict)
    batch_size: int = 20
    specs: Mapping[DocumentType, ExtractionSpec] = field(
        default_factory=lambda: dict(EXTRACTION_SPECS)
    )

    def targets(self) -> dict[str, str]:
        return {doc_type.value: spec.prompt_ref for doc_type, spec in self.specs.items()}

    async def run_once(self) -> ExtractionOutcome:
        outcome = ExtractionOutcome()
        for item in await self.queue.awaiting(self.targets(), self.batch_size):
            await self.extract(item, outcome)
        return outcome

    async def extract(self, item: QueuedDocument, outcome: ExtractionOutcome) -> None:
        context = lane_context(item.tenant_id, item.workspace_id)
        document = await self.documents.get(context, CaseDocumentId(item.document_id))
        if (
            document is None
            or document.tenant_id != item.tenant_id
            or document.workspace_id != item.workspace_id
        ):
            # Not this workspace's document: nothing is read, nothing is called.
            logger.warning("extraction refused: document not in its queued workspace")
            outcome.refused_before_reading += 1
            return
        spec = self.specs.get(document.doc_type)
        if spec is None:
            outcome.refused_before_reading += 1
            return
        plan = await self.plans.plan_of(item.tenant_id)
        if plan is None:
            outcome.deferred += 1
            return

        data = await self.storage.get(document.object_key)
        if hashlib.sha256(data).hexdigest() != document.sha256:
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.FAILED,
                error="stored object does not match the document's sha256",
            )
            return
        if not self.text.supports(document.content_type):
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.UNREADABLE,
                error=f"no in-process reader for {document.content_type}",
            )
            return
        try:
            parsed = await self.text.text_of(data, document.content_type, document.filename)
        except Exception:
            logger.warning("extraction: the file could not be read", exc_info=True)
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.UNREADABLE,
                error="the file could not be read",
            )
            return
        if is_unreadable(parsed.text):
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.UNREADABLE,
                error="no readable text",
            )
            return

        redacted = redact_identifiers(parsed.text)
        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=item.tenant_id,
            workspace_id=item.workspace_id,
            actor_id=system_actor(EXTRACTION_LANE).value,
            worker_id=EXTRACTION_WORKER_ID,
            worker_version=EXTRACTION_WORKER_VERSION,
            channel="worker",
            plan_id=plan,
            roles=frozenset(),
            scopes=frozenset(),
            trace_id=str(run_id),
            subject_ref=f"case_document:{document.id}",
        )
        request = ModelRequest(
            task="structured_extraction",
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            variables={DOCUMENT_TEXT_VARIABLE: redacted.text},
            model_profile=self.routes.get(document.doc_type, self.model_profile),
            route_kind="structured_extraction",
        )
        try:
            reading = await self.gateway.generate_structured(
                request, spec.reading, run_context=run_context
            )
        except QuotaExceededError:
            outcome.deferred += 1
            return
        except ModelOutputInvalidError:
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.REFUSED,
                text=redacted.text,
                redactions=redacted.count,
                error="the model's reading did not fit the schema",
            )
            return
        except (InfrastructureError, BudgetExceededError) as exc:
            await self._record(
                context,
                document,
                spec,
                outcome,
                ExtractionStatus.FAILED,
                text=redacted.text,
                redactions=redacted.count,
                error=type(exc).__name__,
            )
            return
        grounded = ground(reading, spec, redacted.text)
        await self._record(
            context,
            document,
            spec,
            outcome,
            ExtractionStatus.EXTRACTED,
            text=redacted.text,
            redactions=redacted.count,
            fields=grounded.fields,
            gaps=gaps_json(grounded.gaps),
        )

    async def _record(
        self,
        context: AccessContext,
        document: CaseDocument,
        spec: ExtractionSpec,
        outcome: ExtractionOutcome,
        status: ExtractionStatus,
        *,
        text: str = "",
        redactions: int = 0,
        fields: dict[str, Any] | None = None,
        gaps: list[dict[str, str]] | None = None,
        error: str | None = None,
    ) -> None:
        extraction = NewExtraction(
            id=self.ids.new_uuid(),
            document_id=document.id.value,
            doc_type=document.doc_type,
            sha256=document.sha256,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            model_profile=self.routes.get(document.doc_type, self.model_profile),
            status=status,
            text=text,
            fields=fields or {},
            gaps=gaps or [],
            redactions=redactions,
            error=None if error is None else error[:500],
        )
        audit = lane_audit_event(
            lane=EXTRACTION_LANE,
            event_id=self.ids.new_uuid(),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            action=DOCUMENT_EXTRACTED,
            resource_type="case_document",
            resource_id=str(document.id),
            occurred_at=self.clock.now(),
            details={
                "extraction_id": str(extraction.id),
                "status": status.value,
                "prompt": spec.prompt_ref,
                "gaps": len(extraction.gaps),
                "redactions": redactions,
            },
        )
        try:
            await self.extractions.add(context, extraction, audit=audit)
        except ConflictError:
            # Another tick read it first; its row stands.
            return
        outcome.count(status)
