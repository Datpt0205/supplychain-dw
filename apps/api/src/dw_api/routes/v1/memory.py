"""Long-term memory inventory API (read-only; writes go through the policy).

`GET /memory/candidates/{id}` is what a reviewer reads before deciding a
`memory.review` approval: the approval carries identifiers and the label only,
and the content is served here, under the reader's clearance.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError
from dw_kernel.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Page, PageQuery, page_request


class MemoryCandidateView(BaseModel):
    candidate_id: uuid.UUID
    worker_id: str
    memory_type: str
    content: str
    structured_facts: dict[str, Any]
    subject_refs: list[str]
    fact_key: str | None
    provenance_refs: list[dict[str, Any]]
    classification: str
    confidence: float
    decision: str
    memory_id: uuid.UUID | None
    created_by_run_id: uuid.UUID
    created_at: datetime


class MemoryItemView(BaseModel):
    memory_id: uuid.UUID
    worker_id: str
    memory_type: str
    content: str
    confidence: float
    classification: str
    provenance_count: int
    valid_from: datetime
    created_by_run_id: uuid.UUID


router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/items", response_model=Page[MemoryItemView])
async def list_items(
    context: RequireAccessContext,
    container: RequireContainer,
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, description="Opaque cursor from a previous page."),
) -> Page[MemoryItemView]:
    if container.memory_service is None:
        raise InfrastructureError("memory service is not configured")
    await container.authorization.require(
        context=context, action="memory.read", resource_type="memory_item"
    )
    request = page_request(
        limit=limit,
        cursor=cursor,
        query=PageQuery(
            key="memory.items",
            filters={"tenant": context.tenant_id, "workspace": context.workspace_id},
        ),
    )
    page = await container.memory_service.list_items(context, request)
    return page.map_items(
        lambda item: MemoryItemView(
            memory_id=item.memory_id,
            worker_id=item.worker_id,
            memory_type=item.memory_type.value,
            content=item.content,
            confidence=item.confidence,
            classification=item.classification,
            provenance_count=len(item.provenance_refs),
            valid_from=item.valid_from,
            created_by_run_id=item.created_by_run_id,
        )
    )


@router.get("/candidates/{candidate_id}", response_model=MemoryCandidateView)
async def get_candidate(
    candidate_id: uuid.UUID, context: RequireAccessContext, container: RequireContainer
) -> MemoryCandidateView:
    if container.memory_service is None:
        raise InfrastructureError("memory service is not configured")
    await container.authorization.require(
        context=context,
        action="memory.read",
        resource_type="memory_candidate",
        resource_id=str(candidate_id),
    )
    # Workspace and clearance are enforced by the service, where the row is read.
    candidate = await container.memory_service.get_candidate(candidate_id, context)
    return MemoryCandidateView(
        candidate_id=candidate.candidate_id,
        worker_id=candidate.worker_id,
        memory_type=candidate.memory_type,
        content=candidate.content,
        structured_facts=dict(candidate.structured_facts),
        subject_refs=list(candidate.subject_refs),
        fact_key=candidate.fact_key,
        provenance_refs=[dict(ref) for ref in candidate.provenance_refs],
        classification=candidate.classification,
        confidence=candidate.confidence,
        decision=candidate.decision,
        memory_id=candidate.memory_id,
        created_by_run_id=candidate.created_by_run_id,
        created_at=candidate.created_at,
    )
