"""Run status + timeline API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.errors import InfrastructureError, NotFoundError


class RunView(BaseModel):
    id: uuid.UUID
    status: str
    worker_id: str
    worker_version: str
    graph_version: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    approval_request_id: uuid.UUID | None
    release_manifest_ref: str | None


class TimelineEvent(BaseModel):
    action: str
    resource_type: str
    resource_id: str
    policy_decision: str | None
    occurred_at: datetime
    details: dict[str, Any]


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("/{run_id}", response_model=RunView)
async def get_run(
    run_id: uuid.UUID, context: RequireAccessContext, container: RequireContainer
) -> RunView:
    if container.run_store is None:
        raise InfrastructureError("runtime is not configured")
    await container.authorization.require(
        context=context,
        action="runs.read",
        resource_type="worker_run",
        resource_id=str(run_id),
    )
    # Read in the caller's workspace: another workspace's run is a 404.
    record = await container.run_store.get(container.run_context_for(context, run_id), run_id)
    return RunView(
        id=record.id,
        status=record.status.value,
        worker_id=record.worker_id,
        worker_version=record.worker_version,
        graph_version=record.graph_version,
        result=record.result,
        error=record.error,
        approval_request_id=record.approval_request_id,
        release_manifest_ref=record.release_manifest_ref,
    )


@router.get("/{run_id}/timeline", response_model=list[TimelineEvent])
async def get_timeline(
    run_id: uuid.UUID, context: RequireAccessContext, container: RequireContainer
) -> list[TimelineEvent]:
    if container.uow_factory is None:
        raise InfrastructureError("database is not configured")
    await container.authorization.require(
        context=context,
        action="runs.read",
        resource_type="worker_run",
        resource_id=str(run_id),
    )
    async with container.uow_factory(context) as uow:
        events = await uow.audit.list_for_run(run_id, workspace_id=context.workspace_id)
    return [
        TimelineEvent(
            action=e.action,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
            policy_decision=e.policy_decision,
            occurred_at=e.occurred_at,
            details=dict(e.details),
        )
        for e in events
    ]


class CancelResult(BaseModel):
    """Whether a run was actually stopped, not whether the request was understood.

    `False` is an ordinary answer: the turn had already finished, or another
    process holds it. A caller pressing Stop twice should get 200 both times —
    the second one describes a thread that is already not running, which is what
    they asked for.
    """

    cancelled: bool


@router.post("/threads/{thread_id}/cancel", response_model=CancelResult)
async def cancel_thread(
    thread_id: uuid.UUID, context: RequireAccessContext, container: RequireContainer
) -> CancelResult:
    """Stop the run in flight on this thread.

    The runner's own `cancel_thread` takes a thread id and nothing else: it
    looks the task up in an in-process dict, which is safe while the only caller
    is a run that started it. Over HTTP the id is whatever the caller typed, so
    ownership is established HERE, against the database under the caller's
    tenant, before the runner is asked to do anything.

    A thread this tenant has never run on is a 404 rather than a 403. "Not
    yours" and "never existed" are the same answer to someone probing ids, and
    the difference between them is exactly what a prober is trying to learn.
    """
    if container.run_store is None or container.runner is None:
        raise InfrastructureError("runtime is not configured")
    await container.authorization.require(
        context=context,
        action="runs.cancel",
        resource_type="worker_run",
        resource_id=str(thread_id),
    )
    if not await container.run_store.thread_belongs_to(
        context.tenant_id, context.workspace_id, thread_id
    ):
        raise NotFoundError("thread not found", details={"thread_id": str(thread_id)})
    return CancelResult(cancelled=await container.runner.cancel_thread(thread_id))
