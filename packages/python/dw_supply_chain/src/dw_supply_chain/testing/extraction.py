"""In-memory ports of the document extraction lane, for its unit tests and its
eval grader (ticket ai-automation/02).

Each keeps its port's promise: `InMemoryExtractionDocuments` answers only under
the reader's tenant AND workspace, as RLS does (`leaky=True` stands in for an
adapter that forgot to, so the lane's own check can be exercised);
`RecordingExtractions` refuses a second reading of one document under one
prompt version, as the UNIQUE does. `ScriptedGateway` renders every request
through a real `PromptRegistry` and keeps the rendering, so a test asserts on
what would reach the model, then answers from its script.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.prompts import PromptRegistry, RenderedPrompt
from dw_agent_runtime.ports import ModelRequest, OutputT
from dw_kernel.errors import ConflictError
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.document_extraction import (
    DocumentText,
    NewExtraction,
    QueuedDocument,
)
from dw_supply_chain.domain.case_document import CaseDocument, CaseDocumentId

PDF = "application/pdf"


@dataclass
class InMemoryExtractionDocuments:
    rows: list[CaseDocument] = field(default_factory=list)
    leaky: bool = False

    async def get(self, context: AccessContext, document_id: CaseDocumentId) -> CaseDocument | None:
        for row in self.rows:
            if row.id != document_id:
                continue
            if self.leaky or (
                row.tenant_id == context.tenant_id and row.workspace_id == context.workspace_id
            ):
                return row
        return None


@dataclass
class InMemoryObjects:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def get(self, key: str) -> bytes:
        return self.objects[key]


class PlainTextReader:
    """Reads a "PDF" whose bytes are its UTF-8 text; nothing else is read."""

    def supports(self, content_type: str) -> bool:
        return content_type == PDF

    async def text_of(self, data: bytes, content_type: str, filename: str) -> DocumentText:
        return DocumentText(text=data.decode("utf-8"))


@dataclass
class RecordingExtractions:
    rows: list[tuple[AccessContext, NewExtraction]] = field(default_factory=list)
    audits: list[AuditEvent] = field(default_factory=list)

    async def add(
        self, context: AccessContext, extraction: NewExtraction, *, audit: AuditEvent
    ) -> None:
        if any(
            e.document_id == extraction.document_id
            and (e.prompt_id, e.prompt_version) == (extraction.prompt_id, extraction.prompt_version)
            for _, e in self.rows
        ):
            raise ConflictError("already read under this prompt version")
        self.rows.append((context, extraction))
        self.audits.append(audit)


@dataclass
class ListQueue:
    items: list[QueuedDocument] = field(default_factory=list)

    async def awaiting(self, targets: Mapping[str, str], limit: int) -> list[QueuedDocument]:
        return self.items[:limit]


@dataclass
class StaticPlans:
    plans: dict[uuid.UUID, str] = field(default_factory=dict)

    async def plan_of(self, tenant_id: uuid.UUID) -> str | None:
        return self.plans.get(tenant_id)


@dataclass
class RecordingGateway:
    """A real gateway (a model gate run, ticket ai-automation/06), with every
    request rendered through `registry` and kept, as `ScriptedGateway` keeps
    them, so the same checks run on what a live model was sent."""

    registry: PromptRegistry
    inner: Any
    sent: list[RenderedPrompt] = field(default_factory=list)
    contexts: list[RunContext] = field(default_factory=list)

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.sent.append(
            self.registry.render(request.prompt_id, request.prompt_version, request.variables)
        )
        self.contexts.append(run_context)
        answer: OutputT = await self.inner.generate_structured(
            request, output_type, run_context=run_context
        )
        return answer


@dataclass
class ScriptedGateway:
    """Renders through `registry`, keeps the rendering, answers from the
    script: a reading, an exception to raise, or an empty reading."""

    registry: PromptRegistry
    answer: BaseModel | Exception | None = None
    sent: list[RenderedPrompt] = field(default_factory=list)
    contexts: list[RunContext] = field(default_factory=list)

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.sent.append(
            self.registry.render(request.prompt_id, request.prompt_version, request.variables)
        )
        self.contexts.append(run_context)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is None:
            return output_type()
        if not isinstance(self.answer, output_type):
            return output_type.model_validate(self.answer.model_dump())
        return self.answer
