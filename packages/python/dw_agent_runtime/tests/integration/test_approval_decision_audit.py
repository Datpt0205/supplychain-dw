"""Integration: every approval decision is on the audit log, in its own
transaction (platform-runtime/approval-audit-and-workspace/01).

Real Postgres: the paused demo graph raises an approval, `decide` settles it,
and `platform.audit_events` - which `dw_app` may only append to - carries one
`approval.decided` row naming the decider, with the same `decision_id` as the
`approval_decisions` row. A decision refused before anything is written leaves
no row, and an audit write that fails takes the decision down with it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from runtime_harness import RuntimeUrls, make_run_context
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_approval_decider_scope import _ABSENT, Stack, _decider, build_stamping_graph

from dw_agent_runtime.adapters.run_store import RunStatus
from dw_agent_runtime.approval_flow import DECIDED_ACTION, ApproveAndResumeService
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.testing.demo_graph import DEMO_WORKER_YAML
from dw_kernel.errors import ConflictError, PermissionDeniedError
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.adapters.persistence.uow import SqlPlatformUnitOfWork
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent

pytestmark = pytest.mark.integration


@pytest.fixture
def worker_config(tmp_path: Path) -> Path:
    path = tmp_path / "demo_approval.yaml"
    path.write_text(DEMO_WORKER_YAML, encoding="utf-8")
    return path


async def _paused(urls: RuntimeUrls, worker_config: Path) -> tuple[Stack, RunContext, uuid.UUID]:
    run = make_run_context()
    stack = Stack(urls.app, worker_config, build_stamping_graph(_ABSENT))
    await stack.runner.start(run_context=run, input_payload={"subject": "x"})
    record = await stack.run_store.get(run, run.run_id)
    assert record.approval_request_id is not None
    return stack, run, record.approval_request_id


async def _rows(stack: Stack, run: RunContext, sql: str, approval_id: object) -> list[Any]:
    async with stack.engine.begin() as conn:
        await conn.execute(
            sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(run.tenant_id)}
        )
        return list((await conn.execute(sa.text(sql), {"a": approval_id})).all())


async def _decided_rows(stack: Stack, run: RunContext, approval_id: uuid.UUID) -> list[Any]:
    return await _rows(
        stack,
        run,
        "SELECT actor_id, details, run_id, occurred_at FROM platform.audit_events"
        f" WHERE action = '{DECIDED_ACTION}' AND resource_id = :a",
        str(approval_id),
    )


async def _decisions(stack: Stack, run: RunContext, approval_id: uuid.UUID) -> list[Any]:
    return await _rows(
        stack,
        run,
        "SELECT id, decided_by, decided_at FROM platform.approval_decisions WHERE request_id = :a",
        approval_id,
    )


@pytest.mark.parametrize(
    ("as_requester", "approve", "outcome", "withdrawn"),
    [
        (False, True, "approved", False),
        (False, False, "rejected", False),
        (True, False, "rejected", True),
    ],
    ids=["approved", "rejected", "withdrawn"],
)
async def test_a_decision_writes_one_audit_row_naming_the_decider(
    urls: RuntimeUrls,
    worker_config: Path,
    as_requester: bool,
    approve: bool,
    outcome: str,
    withdrawn: bool,
) -> None:
    stack, run, approval_id = await _paused(urls, worker_config)
    decider = _decider(
        tenant=run.tenant_id,
        workspace=run.workspace_id,
        principal=run.actor_id if as_requester else None,
    )

    await stack.approvals.decide(
        approval_id=approval_id,
        approve=approve,
        comment="ghi chú",
        context=decider,
        authorization=ScopeAuthorizationService(),
    )

    [(decision_id, decided_by, decided_at)] = await _decisions(stack, run, approval_id)
    [(actor_id, details, run_id, occurred_at)] = await _decided_rows(stack, run, approval_id)
    assert actor_id == decided_by == decider.principal_id
    assert run_id == run.run_id
    assert occurred_at == decided_at
    assert details == {
        "approval_type": "demo.dispatch",
        "outcome": outcome,
        "decision_id": str(decision_id),
        "withdrawn": withdrawn,
    }
    await stack.dispose()


async def test_a_refused_decision_writes_no_audit_row(
    urls: RuntimeUrls, worker_config: Path
) -> None:
    stack, run, approval_id = await _paused(urls, worker_config)
    no_right = AccessContext(
        tenant_id=run.tenant_id,
        workspace_id=run.workspace_id,
        principal_id=uuid.uuid4(),
        roles=frozenset({"member"}),
        scopes=frozenset(),
        plan_id="professional",
    )

    with pytest.raises(PermissionDeniedError):
        await stack.approvals.decide(
            approval_id=approval_id,
            approve=False,
            comment="",
            context=no_right,
            authorization=ScopeAuthorizationService(),
        )
    # Strict, decided by its own requester.
    strict = ApproveAndResumeService(
        uow_factory=stack.uow_factory,
        runner=stack.runner,
        run_store=stack.run_store,
        clock=SystemClock(),
        id_generator=Uuid4Generator(),
        strict_approval_prefixes=frozenset({"demo."}),
    )
    with pytest.raises(ConflictError, match="separation of duties"):
        await strict.decide(
            approval_id=approval_id,
            approve=True,
            comment="ok",
            context=_decider(
                tenant=run.tenant_id, workspace=run.workspace_id, principal=run.actor_id
            ),
            authorization=ScopeAuthorizationService(),
        )

    assert await _decided_rows(stack, run, approval_id) == []
    assert await _decisions(stack, run, approval_id) == []
    await stack.dispose()


class _AuditDown:
    async def append(self, event: AuditEvent) -> None:
        raise RuntimeError("audit write failed")


class _AuditFails(SqlPlatformUnitOfWork):
    async def __aenter__(self) -> _AuditFails:
        await super().__aenter__()
        self.audit = _AuditDown()  # type: ignore[assignment]
        return self


class _CommitFails(SqlPlatformUnitOfWork):
    async def commit(self) -> None:
        raise RuntimeError("commit failed")


@pytest.mark.parametrize("uow_class", [_AuditFails, _CommitFails], ids=["append", "commit"])
async def test_no_decision_without_its_audit_row_and_no_row_without_the_decision(
    urls: RuntimeUrls, worker_config: Path, uow_class: type[SqlPlatformUnitOfWork]
) -> None:
    stack, run, approval_id = await _paused(urls, worker_config)
    sessions = async_sessionmaker(stack.engine, class_=AsyncSession, expire_on_commit=False)
    failing = ApproveAndResumeService(
        uow_factory=lambda context: uow_class(sessions, context),
        runner=stack.runner,
        run_store=stack.run_store,
        clock=SystemClock(),
        id_generator=Uuid4Generator(),
    )

    with pytest.raises(RuntimeError, match="failed"):
        await failing.decide(
            approval_id=approval_id,
            approve=True,
            comment="",
            context=_decider(tenant=run.tenant_id, workspace=run.workspace_id),
            authorization=ScopeAuthorizationService(),
        )

    assert await _decisions(stack, run, approval_id) == []
    assert await _decided_rows(stack, run, approval_id) == []
    [(status,)] = await _rows(
        stack, run, "SELECT status FROM platform.approval_requests WHERE id = :a", approval_id
    )
    assert status == "pending"
    assert (await stack.run_store.get(run, run.run_id)).status is RunStatus.WAITING_APPROVAL
    await stack.dispose()
