"""The approval-gated PO-case transition graph, pause/resume proven for real.

Uses LangGraph's own in-memory checkpointer (`MemorySaver`) rather than
Postgres — this needs no real database, so it stays a unit test, even
though it exercises real `interrupt()`/`Command(resume=...)` semantics, not
a mock of them. `packages/python/dw_supply_chain/tests/integration/
test_advance_case_approval.py` proves the same cycle through the real
`LangGraphWorkflowRunner` + Postgres checkpointer, including a process
restart between pause and resume — what only a real database can prove.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from dw_agent_runtime.contracts import RunContext
from dw_kernel.errors import ConflictError, DomainError, NotFoundError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.pagination import Page, PageRequest
from dw_kernel.ports import SystemClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.ports import POCaseListFilter
from dw_supply_chain.domain.packaging_design import PreProductionTest, ProductionGate
from dw_supply_chain.domain.po_case import CaseState, CaseTransition, POCase, POCaseId
from dw_supply_chain.testing.production_gate import open_production_gate
from dw_supply_chain.workflows.advance_case_graph import (
    APPROVAL_TYPE_PREFIX,
    build_advance_case_graph,
)

pytestmark = pytest.mark.unit


class FakePOCaseRepository:
    def __init__(self, case: POCase) -> None:
        self.case = case
        self.saved: POCase | None = None
        self.audits: list[AuditEvent] = []

    async def add(self, context: AccessContext, case: POCase, *, audit: AuditEvent) -> None:
        raise NotImplementedError("not exercised by this graph")

    async def get(self, context: AccessContext, case_id: POCaseId) -> POCase | None:
        return self.case if case_id.value == self.case.id.value else None

    async def save(self, context: AccessContext, case: POCase, *, audit: AuditEvent) -> None:
        self.saved = case
        self.audits.append(audit)

    async def get_current_state_entered_at(self, context: AccessContext, case_id: POCaseId) -> None:
        raise NotImplementedError("not exercised by this graph")

    async def list_page(
        self, context: AccessContext, request: PageRequest, case_filter: POCaseListFilter
    ) -> Page[POCase]:
        raise NotImplementedError("not exercised by this graph")

    async def list_transitions(
        self, context: AccessContext, case_id: POCaseId, request: PageRequest
    ) -> Page[CaseTransition]:
        raise NotImplementedError("not exercised by this graph")

    async def list_supplier_names(self, context: AccessContext) -> list[str]:
        raise NotImplementedError("not exercised by this graph")

    async def find_by_reference(self, context: AccessContext, po_reference: str) -> list[POCase]:
        raise NotImplementedError("not exercised by this graph")

    async def bulk_current_state_entered_at(
        self, context: AccessContext, case_ids: list[POCaseId]
    ) -> dict[uuid.UUID, datetime]:
        raise NotImplementedError("not exercised by this graph")

    async def get_many(self, context: AccessContext, case_ids: list[POCaseId]) -> list[POCase]:
        raise NotImplementedError("not exercised by this graph")

    async def list_latest_transitions_since(
        self, context: AccessContext, since: datetime, *, limit: int
    ) -> tuple[int, list[tuple[POCaseId, CaseTransition]]]:
        raise NotImplementedError("not exercised by this graph")


def _case(**overrides: object) -> POCase:
    defaults: dict[str, object] = {
        "id": POCaseId(uuid.uuid4()),
        "tenant_id": TenantId(uuid.uuid4()),
        "workspace_id": WorkspaceId(uuid.uuid4()),
        "po_reference": "PO-0001",
        "supplier_name": "Elmich Co.",
    }
    defaults.update(overrides)
    return POCase(**defaults)  # type: ignore[arg-type]


def _run_context(*, tenant_id: uuid.UUID, workspace_id: uuid.UUID) -> RunContext:
    run_id = uuid.uuid4()
    return RunContext(
        run_id=run_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=uuid.uuid4(),
        worker_id="supply_chain_advance_case",
        worker_version="1.0.0",
        channel="web",
        plan_id="professional",
        roles=frozenset({"member"}),
        scopes=frozenset({"supply_chain.po_case.write"}),
        trace_id=str(run_id),
    )


def _config(run_context: RunContext) -> dict[str, object]:
    return {"configurable": {"thread_id": str(run_context.run_id)}}


class _ClosedGate:
    """Step 13's gate for a tenant that requires the test, on a case whose
    test is not passed (slice PK)."""

    async def for_case(self, context: AccessContext, po_case_id: uuid.UUID) -> ProductionGate:
        return ProductionGate(required=True, test=PreProductionTest.FAILED)


def _compiled(repo: FakePOCaseRepository, gate: Any = None) -> Any:
    # Typed `Any` on purpose, matching `LangGraphWorkflowRunner._graph`'s own
    # return type: a `StateGraph` compiled with full generics makes mypy
    # check `ainvoke`'s `context=` kwarg against a graph-level context type
    # this module never parameterises (same reasoning `build_advance_case_
    # graph`'s own `# type: ignore[type-arg]` already documents), not a
    # real type hole in the graph or in `RunContext` itself.
    return build_advance_case_graph(
        repo, Uuid4Generator(), SystemClock(), gate or open_production_gate()
    ).compile(checkpointer=MemorySaver())


async def test_starting_a_run_pauses_before_anything_is_applied() -> None:
    case = _case()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)

    state = await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "request_deposit", "reason": None},
        _config(run_context),
        context=run_context,
    )

    assert "__interrupt__" in state
    payload = state["__interrupt__"][0].value
    assert payload["approval_type"] == f"{APPROVAL_TYPE_PREFIX}request_deposit"
    assert payload["po_case_id"] == str(case.id)
    assert payload["action"] == "request_deposit"
    assert repo.saved is None  # nothing applied yet


async def test_approving_the_resume_applies_the_transition() -> None:
    case = _case()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "request_deposit", "reason": None},
        config,
        context=run_context,
    )

    final_state = await graph.ainvoke(
        Command(resume={"approved": True, "comment": "ok, tiến hành"}), config, context=run_context
    )

    assert final_state["applied"] is True
    assert repo.saved is not None
    assert repo.saved.state is CaseState.WAITING_DEPOSIT
    # Ticket P2: the step's audit event, under the requester's own context.
    (audit,) = repo.audits
    assert (audit.action, audit.resource_id) == (
        "supply_chain.po_case.request_deposit",
        str(case.id),
    )
    assert (audit.tenant_id.value, audit.workspace_id.value, audit.actor_id.value) == (
        run_context.tenant_id,
        run_context.workspace_id,
        run_context.actor_id,
    )
    assert audit.details["run_id"] == str(run_context.run_id)


async def test_rejecting_the_resume_applies_nothing() -> None:
    case = _case()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "request_deposit", "reason": None},
        config,
        context=run_context,
    )

    final_state = await graph.ainvoke(
        Command(resume={"approved": False, "comment": "không đủ evidence"}),
        config,
        context=run_context,
    )

    assert final_state["applied"] is False
    assert repo.saved is None
    assert repo.audits == []
    assert case.state is CaseState.PO_CREATED


async def test_an_illegal_transition_fails_the_run_rather_than_silently_applying() -> None:
    """The case moved on (e.g. another actor already advanced it) between
    the approval request and the decision — apply_action's own guard must
    still refuse, not silently accept a transition that is no longer legal."""
    case = _case(state=CaseState.COMPLETED)
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "request_deposit", "reason": None},
        config,
        context=run_context,
    )

    with pytest.raises(ConflictError):
        await graph.ainvoke(
            Command(resume={"approved": True, "comment": "ok"}), config, context=run_context
        )
    assert repo.saved is None


async def test_a_reason_required_action_without_a_reason_fails_the_apply_node() -> None:
    case = _case()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "cancel", "reason": None},
        config,
        context=run_context,
    )

    with pytest.raises(DomainError, match="reason"):
        await graph.ainvoke(
            Command(resume={"approved": True, "comment": "ok"}), config, context=run_context
        )
    assert repo.saved is None


async def test_an_unknown_case_id_fails_the_apply_node() -> None:
    case = _case()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo)
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(uuid.uuid4()), "action": "request_deposit", "reason": None},
        config,
        context=run_context,
    )

    with pytest.raises(NotFoundError):
        await graph.ainvoke(
            Command(resume={"approved": True, "comment": "ok"}), config, context=run_context
        )


async def test_an_approved_start_production_still_asks_the_gate_when_applied() -> None:
    """Slice PK: the graph's apply node is the second door to step 13. An
    approval decided while the required test is not passed applies nothing."""
    case = _case()
    case.request_deposit()
    case.confirm_deposit()
    case.start_pre_production()
    repo = FakePOCaseRepository(case)
    graph = _compiled(repo, _ClosedGate())
    run_context = _run_context(tenant_id=case.tenant_id.value, workspace_id=case.workspace_id.value)
    config = _config(run_context)
    await graph.ainvoke(
        {"po_case_id": str(case.id), "action": "start_production", "reason": None},
        config,
        context=run_context,
    )

    with pytest.raises(ConflictError, match="test trước sản xuất"):
        await graph.ainvoke(
            Command(resume={"approved": True, "comment": "duyệt"}), config, context=run_context
        )

    assert repo.saved is None
    assert case.state is CaseState.PRE_PRODUCTION
