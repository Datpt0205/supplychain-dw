"""Handlers: the only place this context decides anything.

Authorization is checked here, where the read/write actually happens, not
only in the route that calls in — a future caller that reaches these
handlers some other way (a tool, the eventual AI command bar) gets the same
check for free, and none can bypass it by skipping a layer above.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from pydantic import BaseModel

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError, WorkflowRunnerPort
from dw_kernel.errors import DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import Page, page_request
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty, SupplyChainActionDuties
from dw_supply_chain.application.ports import (
    DelayImpactAnalysisRepositoryPort,
    FollowUpRecord,
    FollowUpRepositoryPort,
    PendingApprovalRecord,
    PendingApprovalsPort,
    POCaseListFilter,
    POCaseRepositoryPort,
    SupplierUpdateRepositoryPort,
)
from dw_supply_chain.approval_matrix import SupplyChainApprovalMatrix
from dw_supply_chain.brief_policy import SupplyChainBriefPolicy
from dw_supply_chain.domain.brief_summary import (
    BriefSummary,
    BriefSummaryStatus,
    ground_summary,
)
from dw_supply_chain.domain.case_query import (
    CaseQueryKind,
    CaseQueryOutcome,
    CaseQueryPlan,
    GroundedField,
    ground,
    ignored_fields,
    plan_case_query,
)
from dw_supply_chain.domain.daily_brief import (
    CHANGE_WINDOW_HOURS,
    DailyBrief,
    PendingApprovalsSeen,
    PendingCaseApproval,
    RecentChange,
    compose_brief,
)
from dw_supply_chain.domain.delay_impact import (
    DelayImpactAnalysis,
    DelayImpactAnalysisId,
    propagated_estimates,
)
from dw_supply_chain.domain.missing_update import (
    MissingUpdateAssessment,
    missing_update_status,
)
from dw_supply_chain.domain.po_case import (
    REASON_REQUIRED_ACTIONS,
    CaseAction,
    CaseTransition,
    POCase,
    POCaseId,
    apply_action,
)
from dw_supply_chain.domain.portfolio import CaseHealth, PortfolioSummary, summarize_portfolio
from dw_supply_chain.domain.sla_evaluation import SLAEvaluation, evaluate_sla
from dw_supply_chain.domain.supplier_update import (
    SupplierUpdate,
    SupplierUpdateId,
    requires_confirmation,
)
from dw_supply_chain.follow_up_policy import SupplyChainFollowUpPolicy
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
)
from dw_supply_chain.product_approvals import (
    PRODUCT_APPROVALS_POLICY_ID,
    SupplyChainProductApprovals,
)
from dw_supply_chain.sla_policy import SupplyChainSLAPolicy
from dw_supply_chain.workflows.advance_case_graph import APPROVAL_TYPE_PREFIX
from dw_supply_chain.workflows.brief_summary import summarize_brief
from dw_supply_chain.workflows.case_query_understanding import understand_case_query
from dw_supply_chain.workflows.delay_impact_analysis import analyze_delay_impact
from dw_supply_chain.workflows.supplier_update_understanding import understand_supplier_update

PO_CASE_READ = "supply_chain.po_case.read"
# Opening a new case. Taking a step on one is a duty (`duty_scope`).
PO_CASE_WRITE = "supply_chain.po_case.write"
_RESOURCE = "po_case"

SUPPLIER_UPDATE_READ = "supply_chain.supplier_update.read"
SUPPLIER_UPDATE_WRITE = "supply_chain.supplier_update.write"
_SUPPLIER_UPDATE_RESOURCE = "supplier_update"

DELAY_IMPACT_READ = "supply_chain.delay_impact.read"
DELAY_IMPACT_WRITE = "supply_chain.delay_impact.write"
_DELAY_IMPACT_RESOURCE = "delay_impact_analysis"

SLA_POLICY_READ = "supply_chain.sla_policy.read"
SLA_POLICY_WRITE = "supply_chain.sla_policy.write"
_SLA_POLICY_RESOURCE = "sla_policy"
# `PolicyOverridePort.get`/`.put`'s own `policy_id` key. Matches
# `configs/policies/supply_chain_sla@*.yaml`'s own `policy_id` field
# value, not by coincidence — a tenant's override and the platform default
# are the same document type under two different sources.
_SLA_POLICY_ID = "supply_chain_sla"

ACTION_DUTIES_READ = "supply_chain.action_duties.read"
ACTION_DUTIES_WRITE = "supply_chain.action_duties.write"
_ACTION_DUTIES_RESOURCE = "action_duties"
# Matches configs/policies/supply_chain_action_duties@1.0.0.yaml's policy_id.
_ACTION_DUTIES_POLICY_ID = "supply_chain_action_duties"


def duty_scope(duty: CaseDuty) -> str:
    """The scope a caller must hold to take a step of `duty` — what a
    role grants and what a separation-of-duties rule names."""
    return f"supply_chain.duty.{duty.value}"


# Case documents (`application.case_documents`). Declared here because this
# module is where the role catalogue test collects every scope the context
# checks.
DOCUMENT_READ = "supply_chain.document.read"
DOCUMENT_WRITE = "supply_chain.document.write"

# Product-development cases (`application.product_cases`), declared here for
# the same reason. Reading every case of the workspace. Opening a case, as
# `PO_CASE_WRITE` for a PO case (lead decision 9); `propose` is also a step of
# `SupplyChainProductActionDuties`, so it asks its duty as well. Every other
# step is gated by its duty alone.
PRODUCT_CASE_READ = "supply_chain.product_case.read"
PRODUCT_CASE_WRITE = "supply_chain.product_case.write"

FOLLOW_UP_POLICY_READ = "supply_chain.follow_up_policy.read"
FOLLOW_UP_POLICY_WRITE = "supply_chain.follow_up_policy.write"
_FOLLOW_UP_POLICY_RESOURCE = "follow_up_policy"
# Matches configs/policies/supply_chain_follow_ups@1.0.0.yaml's policy_id.
FOLLOW_UP_POLICY_ID = "supply_chain_follow_ups"
_FOLLOW_UP_RESOURCE = "follow_up"

BRIEF_POLICY_READ = "supply_chain.brief_policy.read"
BRIEF_POLICY_WRITE = "supply_chain.brief_policy.write"
_BRIEF_POLICY_RESOURCE = "brief_policy"
# Matches configs/policies/supply_chain_brief@1.0.0.yaml's own policy_id.
_BRIEF_POLICY_ID = "supply_chain_brief"

# The platform approval inbox's own read scope (`GET /api/v1/approvals`). The
# brief shows pending approvals only to a caller who could open that inbox.
APPROVALS_READ = "approvals.read"
# How many pending approvals the brief reads and resolves to a case; the
# group's count is still every pending one.
_BRIEF_APPROVALS_READ = 50

# Neither is a registered worker (no configs/workers/supply_chain.yaml) —
# each is one structured-extraction call, not an autonomous agent loop with
# its own tools or approval policy, so neither needs a worker registration
# to be a valid RunContext. See each workflow module's own docstring.
_SUPPLIER_UPDATE_WORKER_ID = "supply_chain.supplier_update_understanding"
_DELAY_IMPACT_WORKER_ID = "supply_chain.delay_impact_analysis"
_CASE_QUERY_WORKER_ID = "supply_chain.case_query_understanding"
_BRIEF_SUMMARY_WORKER_ID = "supply_chain.daily_brief_summary"
_WORKER_VERSION = "1.0.0"


@dataclass(frozen=True)
class CreatePOCase:
    """Opens a new PO case.

    Tenant and workspace come from the verified context, never from the
    request body — a caller who could name the tenant a case belongs to
    could create cases inside a tenant that is not theirs.
    """

    repo: POCaseRepositoryPort
    authz: AuthorizationPort
    ids: IdGenerator

    async def handle(
        self, context: AccessContext, *, po_reference: str, supplier_name: str
    ) -> POCase:
        await self.authz.require(context=context, action=PO_CASE_WRITE, resource_type=_RESOURCE)
        case = POCase(
            id=POCaseId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            po_reference=po_reference,
            supplier_name=supplier_name,
        )
        await self.repo.add(context, case)
        return case


@dataclass(frozen=True)
class GetPOCase:
    repo: POCaseRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: POCaseId) -> POCase:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await self.repo.get(context, case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
        return case


@dataclass(frozen=True)
class ListPOCases:
    """The caller's tenant's cases, newest first, narrowed by `case_filter`
    — the Case Workspace's own entry point and the Control Tower's drill-
    down. Reuses `PO_CASE_READ`, not resource-scoped since this names no
    single case; a filter only ever narrows what that scope already reads.

    Takes the raw `limit`/`cursor` rather than a ready `PageRequest`: the
    cursor is decoded against `case_filter.page_query()` HERE, so no caller
    (the route today, the AI command bar next) can hand over one filter for
    the SQL and a cursor minted under another."""

    repo: POCaseRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self,
        context: AccessContext,
        case_filter: POCaseListFilter,
        *,
        limit: int,
        cursor: str | None,
    ) -> Page[POCase]:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)
        request = page_request(
            limit=limit, cursor=cursor, query=case_filter.page_query(context.tenant_id)
        )
        if case_filter.matches_nothing:
            return Page(items=(), next_cursor=None)
        return await self.repo.list_page(context, request, case_filter)


@dataclass(frozen=True)
class ListCaseTransitions:
    """The case's own timeline — every state change it has been through,
    oldest first. Reuses `PO_CASE_READ`, resource-scoped like `GetPOCase`:
    this is about one named case, not a tenant-wide listing."""

    repo: POCaseRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: POCaseId) -> list[CaseTransition]:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(case_id),
        )
        case = await self.repo.get(context, case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
        return await self.repo.list_transitions(context, case_id)


@dataclass(frozen=True)
class SubmitSupplierUpdate:
    """Supplier Update Understanding: reads one raw supplier message and
    keeps the record.

    Never mutates the `POCase` itself. The strategy doc's own framing —
    "Workflow engine quyết định state; AI hỗ trợ exception/reasoning" —
    means understanding a message is not the same decision as acting on it;
    what (if anything) to do about a PRODUCTION_DELAY extraction is a later,
    separate step, not automatic here.
    """

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    gateway: ModelGateway
    authz: AuthorizationPort
    ids: IdGenerator

    async def handle(
        self, context: AccessContext, *, po_case_id: POCaseId, raw_text: str
    ) -> SupplierUpdate:
        await self.authz.require(
            context=context,
            action=SUPPLIER_UPDATE_WRITE,
            resource_type=_SUPPLIER_UPDATE_RESOURCE,
            resource_id=str(po_case_id),
        )
        # Read through POCaseRepositoryPort (RLS-scoped) rather than trusting
        # the FK supplier_updates.po_case_id carries: the FK only proves the
        # id exists somewhere, across every tenant, not that it is this
        # caller's case.
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})

        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_SUPPLIER_UPDATE_WORKER_ID,
            worker_version=_WORKER_VERSION,
            channel="web",
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        extraction = await understand_supplier_update(self.gateway, run_context, raw_text)

        update = SupplierUpdate(
            id=SupplierUpdateId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            po_case_id=po_case_id,
            raw_text=raw_text,
            extraction=extraction,
            requires_confirmation=requires_confirmation(extraction, raw_text),
        )
        await self.supplier_update_repo.add(context, update)
        return update


@dataclass(frozen=True)
class ListSupplierUpdates:
    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, po_case_id: POCaseId) -> list[SupplierUpdate]:
        await self.authz.require(
            context=context,
            action=SUPPLIER_UPDATE_READ,
            resource_type=_SUPPLIER_UPDATE_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})
        return await self.supplier_update_repo.list_for_case(context, po_case_id)


@dataclass(frozen=True)
class AnalyzeDelayImpact:
    """Delay Impact Analysis: given a supplier update that already reported
    a delay, finds which milestones are downstream
    (`domain.delay_impact.propagated_estimates` — no model call, see that
    module's own docstring) and asks the model only for assumptions and
    mitigation options.

    Takes `supplier_update_id`, not a raw delay figure the caller supplies —
    the analysis has to be grounded in a `SupplierUpdate` already on record;
    that record IS the "source evidence" the analysis must cite, not a number
    trusted from the request body.
    """

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    delay_impact_repo: DelayImpactAnalysisRepositoryPort
    gateway: ModelGateway
    authz: AuthorizationPort
    ids: IdGenerator

    async def handle(
        self,
        context: AccessContext,
        *,
        po_case_id: POCaseId,
        supplier_update_id: SupplierUpdateId,
    ) -> DelayImpactAnalysis:
        await self.authz.require(
            context=context,
            action=DELAY_IMPACT_WRITE,
            resource_type=_DELAY_IMPACT_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})

        update = await self.supplier_update_repo.get(context, supplier_update_id)
        if update is None or update.po_case_id != po_case_id:
            # Same refusal for "does not exist" and "belongs to a different
            # case": a caller who could tell the two apart could probe which
            # update ids exist on cases that are not theirs to name.
            raise NotFoundError(
                "supplier update not found for this case",
                details={"supplier_update_id": str(supplier_update_id)},
            )
        if update.extraction.delay_days is None:
            raise DomainError(
                "this supplier update reports no delay to analyze",
                details={"supplier_update_id": str(supplier_update_id)},
            )

        delay_days = update.extraction.delay_days
        impacted = propagated_estimates(case, delay_days=delay_days)
        if not impacted:
            raise DomainError(
                "no downstream milestones left to analyze",
                details={"case_id": str(po_case_id), "state": case.state.value},
            )

        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_DELAY_IMPACT_WORKER_ID,
            worker_version=_WORKER_VERSION,
            channel="web",
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        extraction = await analyze_delay_impact(
            self.gateway,
            run_context,
            case=case,
            raw_text=update.raw_text,
            delay_days=delay_days,
            impacted=impacted,
        )

        analysis = DelayImpactAnalysis(
            id=DelayImpactAnalysisId(self.ids.new_uuid()),
            tenant_id=TenantId(context.tenant_id),
            workspace_id=WorkspaceId(context.workspace_id),
            po_case_id=po_case_id,
            supplier_update_id=supplier_update_id,
            delay_days=delay_days,
            impacted_milestones=tuple(impacted),
            extraction=extraction,
        )
        await self.delay_impact_repo.add(context, analysis)
        return analysis


@dataclass(frozen=True)
class ListDelayImpactAnalyses:
    po_case_repo: POCaseRepositoryPort
    delay_impact_repo: DelayImpactAnalysisRepositoryPort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, po_case_id: POCaseId
    ) -> list[DelayImpactAnalysis]:
        await self.authz.require(
            context=context,
            action=DELAY_IMPACT_READ,
            resource_type=_DELAY_IMPACT_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})
        return await self.delay_impact_repo.list_for_case(context, po_case_id)


@dataclass(frozen=True)
class GetMissingUpdateStatus:
    """Missing Update Detection: has this case gone quiet for long enough
    to chase.

    Pure computation over records this context already has — no new table,
    no model call (see `domain.missing_update`'s own docstring). Reuses
    `PO_CASE_READ`: this derives information about an existing case rather
    than a new kind of record with its own access policy.
    """

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    # The tenant's SLA policy carries its reminder/escalation cadence.
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext, po_case_id: POCaseId) -> MissingUpdateAssessment:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})
        assert case.created_at is not None  # persisted rows always carry it

        updates = await self.supplier_update_repo.list_for_case(context, po_case_id)
        last_update_at = updates[0].created_at if updates else None
        policy = await _resolve_sla_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )

        return missing_update_status(
            state=case.state,
            case_created_at=case.created_at,
            last_supplier_update_at=last_update_at,
            now=self.clock.now(),
            cadence=policy.supplier_update.cadence,
        )


APPROVAL_MATRIX_READ = "supply_chain.approval_matrix.read"
APPROVAL_MATRIX_WRITE = "supply_chain.approval_matrix.write"
_APPROVAL_MATRIX_RESOURCE = "approval_matrix"
# Matches configs/policies/supply_chain_approval_matrix@1.0.0.yaml's own
# policy_id — same reasoning as _SLA_POLICY_ID below.
_APPROVAL_MATRIX_POLICY_ID = "supply_chain_approval_matrix"

_ADVANCE_CASE_WORKER_ID = "supply_chain_advance_case"
_ADVANCE_CASE_WORKER_VERSION = "1.0.0"


async def _resolve_policy[PolicyT: BaseModel](
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    *,
    policy_id: str,
    schema: type[PolicyT],
    platform_default: PolicyT,
) -> PolicyT:
    """The tenant's own version of a policy if they have set one, the
    platform default otherwise — the read half of `dw_kernel.overlay.
    TenantOverlay`'s own "a policy" artifact kind, shared by every
    tenant-configurable policy here (SLA, approval matrix, brief order).

    A tenant's override is validated against the SAME schema the platform
    default already is. A document written by a `Set…Override` handler
    already passed this once, but re-validating on read costs nothing and
    means a row edited directly in the database (bypassing the handler) is
    refused loudly rather than trusted."""
    override = await policy_override_repo.get(context, policy_id)
    if override is None:
        return platform_default
    return schema.model_validate(override)


async def _put_policy_override(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    *,
    policy_id: str,
    policy: BaseModel,
    resource_type: str,
    ids: IdGenerator,
    clock: UtcClock,
) -> None:
    """Replaces the caller's tenant's own version of a policy, whole — never
    a field-level merge with the platform default. The audit event commits
    in the same transaction as the write (`PolicyOverridePort.put` requires
    it), named `<scope prefix>.override_set` after the policy's resource."""
    audit = AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=f"supply_chain.{resource_type}.override_set",
        resource_type=resource_type,
        resource_id=policy_id,
        occurred_at=clock.now(),
    )
    await policy_override_repo.put(context, policy_id, policy.model_dump(mode="json"), audit=audit)


async def _resolve_approval_matrix(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default_matrix: SupplyChainApprovalMatrix,
) -> SupplyChainApprovalMatrix:
    """The tenant's own matrix if they have set one, the platform default
    (nothing gated) otherwise."""
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=_APPROVAL_MATRIX_POLICY_ID,
        schema=SupplyChainApprovalMatrix,
        platform_default=platform_default_matrix,
    )


@dataclass(frozen=True, slots=True)
class CaseActionApplied:
    """The transition already happened — `case` reflects it."""

    case: POCase


@dataclass(frozen=True, slots=True)
class CaseActionPendingApproval:
    """The transition is held for a human decision — nothing on `POCase`
    has changed yet. `run_id` is the same id `GET /api/v1/runs/{run_id}`
    (already generic, no new route needed) and `GET /api/v1/approvals`
    resolve against; a client polls either to learn when this settles."""

    run_id: uuid.UUID


AdvancePOCaseResult = CaseActionApplied | CaseActionPendingApproval


@dataclass(frozen=True)
class AdvancePOCase:
    """Executes one guarded `POCase` transition, chosen from the closed
    `CaseAction` set — or, if the caller's tenant has marked that action as
    approval-required (`SupplyChainApprovalMatrix`), starts a durable,
    checkpointed graph run that pauses for a human decision instead
    (`workflows/advance_case_graph.py`) and returns immediately with
    nothing applied yet.

    `action` is a human's selection from a fixed set of context-sensitive
    buttons a real UI would show for the case's current state — never free
    text a model interprets: the model may interpret, code decides. Whether
    the transition is legal from the case's current state is decided
    nowhere but the method itself (`domain.po_case.apply_action`, the same
    dispatch the graph's own apply node calls once a human approves — one
    owner for "which action calls which method", not one copy per caller).

    Who may take the step is the action's duty under the caller's tenant's
    own `SupplyChainActionDuties`: confirming a payment needs the finance
    duty, not a general write. Checked before anything else, and before an
    approval run is started, so the approval path cannot carry a step past
    a requester who could not take it. The graph run starts only from here.
    """

    repo: POCaseRepositoryPort
    authz: AuthorizationPort
    policy_override_repo: PolicyOverridePort
    platform_default_approval_matrix: SupplyChainApprovalMatrix
    platform_default_action_duties: SupplyChainActionDuties
    runner: WorkflowRunnerPort
    ids: IdGenerator

    async def handle(
        self,
        context: AccessContext,
        *,
        po_case_id: POCaseId,
        action: CaseAction,
        reason: str | None = None,
    ) -> AdvancePOCaseResult:
        duties = await _resolve_policy(
            context,
            self.policy_override_repo,
            policy_id=_ACTION_DUTIES_POLICY_ID,
            schema=SupplyChainActionDuties,
            platform_default=self.platform_default_action_duties,
        )
        await self.authz.require(
            context=context,
            action=duty_scope(duties.duty_for(action)),
            resource_type=_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})

        matrix = await _resolve_approval_matrix(
            context, self.policy_override_repo, self.platform_default_approval_matrix
        )
        if matrix.requires_approval(action):
            run_id = self.ids.new_uuid()
            run_context = RunContext(
                run_id=run_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                actor_id=context.principal_id,
                worker_id=_ADVANCE_CASE_WORKER_ID,
                worker_version=_ADVANCE_CASE_WORKER_VERSION,
                channel="web",
                plan_id=context.plan_id,
                roles=context.roles,
                scopes=context.scopes,
                trace_id=str(run_id),
            )
            await self.runner.start(
                run_context=run_context,
                input_payload={
                    "po_case_id": str(po_case_id),
                    "action": action.value,
                    "reason": reason,
                },
            )
            return CaseActionPendingApproval(run_id=run_id)

        # Blank/missing reason on a REASON_REQUIRED_ACTIONS member is
        # refused here, before apply_action()'s own guard — the caller
        # gets a DomainError with a real taxonomy code, not the domain
        # method's bare ValueError, matching the same check the graph's
        # own apply node does not need to repeat (AdvancePOCase is the
        # only entry point for an action that does NOT need approval).
        if action in REASON_REQUIRED_ACTIONS and (reason is None or not reason.strip()):
            raise DomainError("this action requires a reason", details={"action": action.value})
        apply_action(case, action=action, reason=reason)
        await self.repo.save(context, case)
        return CaseActionApplied(case=case)


@dataclass(frozen=True)
class GetApprovalMatrix:
    """The effective approval matrix for the caller's own tenant — their
    own override if they have set one, the platform default (nothing
    gated) otherwise."""

    policy_override_repo: PolicyOverridePort
    platform_default_matrix: SupplyChainApprovalMatrix
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainApprovalMatrix:
        await self.authz.require(
            context=context, action=APPROVAL_MATRIX_READ, resource_type=_APPROVAL_MATRIX_RESOURCE
        )
        return await _resolve_approval_matrix(
            context, self.policy_override_repo, self.platform_default_matrix
        )


@dataclass(frozen=True)
class SetApprovalMatrixOverride:
    """Replaces the caller's tenant's own approval matrix, whole — same
    full-document-replace reasoning as `SetSLAPolicyOverride` below.
    `matrix` arrives already validated (`SupplyChainApprovalMatrix`,
    `extra="forbid"`, each action checked against the real `CaseAction`
    set) by the route's own request model.
    """

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, matrix: SupplyChainApprovalMatrix) -> None:
        await self.authz.require(
            context=context, action=APPROVAL_MATRIX_WRITE, resource_type=_APPROVAL_MATRIX_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=_APPROVAL_MATRIX_POLICY_ID,
            policy=matrix,
            resource_type=_APPROVAL_MATRIX_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


async def _resolve_sla_policy(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default_policy: SupplyChainSLAPolicy,
) -> SupplyChainSLAPolicy:
    """The tenant's own SLA policy if they have set one, the platform
    default otherwise."""
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=_SLA_POLICY_ID,
        schema=SupplyChainSLAPolicy,
        platform_default=platform_default_policy,
    )


@dataclass(frozen=True)
class GetSLAPolicy:
    """The effective SLA policy for the caller's own tenant — their own
    override if they have set one, the platform default otherwise. What a
    tenant admin reads before deciding whether/how to override it.
    """

    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainSLAPolicy:
        await self.authz.require(
            context=context, action=SLA_POLICY_READ, resource_type=_SLA_POLICY_RESOURCE
        )
        return await _resolve_sla_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )


@dataclass(frozen=True)
class SetSLAPolicyOverride:
    """Replaces the caller's tenant's own SLA policy, whole — never a
    field-level merge with the platform default, matching how every other
    `TenantOverlay`-resolved artifact already resolves (the tenant's own
    version, or the platform's, never blended).

    `policy` arrives already validated into `SupplyChainSLAPolicy` by the
    route's own request model — nothing here re-derives that; this handler's
    only job is authorization and persistence.
    """

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainSLAPolicy) -> None:
        await self.authz.require(
            context=context, action=SLA_POLICY_WRITE, resource_type=_SLA_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=_SLA_POLICY_ID,
            policy=policy,
            resource_type=_SLA_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


@dataclass(frozen=True)
class GetSLAEvaluation:
    """Whether the case's CURRENT state has overrun its reference SLA — pure
    computation over `domain.sla_evaluation.evaluate_sla` (no model call).
    Reuses `PO_CASE_READ`, same reasoning as `GetMissingUpdateStatus`: this
    derives information about an existing case, not a new record type.
    """

    po_case_repo: POCaseRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext, po_case_id: POCaseId) -> SLAEvaluation:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_RESOURCE,
            resource_id=str(po_case_id),
        )
        case = await self.po_case_repo.get(context, po_case_id)
        if case is None:
            raise NotFoundError("PO case not found", details={"case_id": str(po_case_id)})
        assert case.created_at is not None  # persisted rows always carry it

        entered_at = await self.po_case_repo.get_current_state_entered_at(context, po_case_id)
        policy = await _resolve_sla_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )
        return evaluate_sla(
            state=case.state,
            entered_current_state_at=entered_at or case.created_at,
            now=self.clock.now(),
            policy=policy,
        )


async def assess_active_cases(
    context: AccessContext,
    *,
    po_case_repo: POCaseRepositoryPort,
    supplier_update_repo: SupplierUpdateRepositoryPort,
    policy_override_repo: PolicyOverridePort,
    platform_default_policy: SupplyChainSLAPolicy,
    clock: UtcClock,
) -> list[CaseHealth]:
    """Both deterministic signals for every ACTIVE case, in one pass — the
    shared input of the Attention Queue and the Control Tower, so the two
    can never evaluate the same case two different ways.

    `POCaseRepositoryPort.bulk_current_state_entered_at`/
    `SupplierUpdateRepositoryPort.bulk_latest` are the bulk counterparts of
    the single-case methods `GetSLAEvaluation`/`GetMissingUpdateStatus`
    already call, so a tenant with many cases costs three queries here, not
    two queries per case. Each case is evaluated by the exact domain
    functions those two single-case handlers call.
    """
    cases = await po_case_repo.list_active(context)
    if not cases:
        return []

    case_ids = [case.id for case in cases]
    entered_at_by_case = await po_case_repo.bulk_current_state_entered_at(context, case_ids)
    latest_update_by_case = await supplier_update_repo.bulk_latest(context, case_ids)
    policy = await _resolve_sla_policy(context, policy_override_repo, platform_default_policy)
    now = clock.now()

    healths: list[CaseHealth] = []
    for case in cases:
        assert case.created_at is not None  # persisted rows always carry it
        latest_update = latest_update_by_case.get(case.id.value)
        healths.append(
            CaseHealth(
                case=case,
                sla=evaluate_sla(
                    state=case.state,
                    entered_current_state_at=entered_at_by_case.get(case.id.value, case.created_at),
                    now=now,
                    policy=policy,
                ),
                missing_update=missing_update_status(
                    state=case.state,
                    case_created_at=case.created_at,
                    last_supplier_update_at=latest_update.created_at if latest_update else None,
                    now=now,
                    cadence=policy.supplier_update.cadence,
                ),
                latest_update=latest_update,
            )
        )
    return healths


@dataclass(frozen=True, slots=True)
class AttentionItem:
    """One case the Attention Queue flags — `sla`/`missing_update` are set
    only when that signal is the reason the case is here at all, never a
    clean bill of health repeated for every case: `GetSLAEvaluation`'s own
    `NOT_APPLICABLE`/`ON_TRACK`/`NOT_EVALUABLE` results never appear here,
    since nothing about them needs attention."""

    case: POCase
    sla: SLAEvaluation | None
    missing_update: MissingUpdateAssessment | None


@dataclass(frozen=True)
class GetAttentionQueue:
    """Deterministic signals only — SLA breach, missing-update reminder/
    escalation — never a risk invented or ranked by a model. Reuses
    `PO_CASE_READ`, tenant-wide like `ListPOCases`, not resource-scoped:
    this names no single case. Which cases are flagged is `CaseHealth`'s
    own decision (`domain.portfolio`), the same one the Control Tower
    counts with.
    """

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext) -> list[AttentionItem]:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)
        healths = await assess_active_cases(
            context,
            po_case_repo=self.po_case_repo,
            supplier_update_repo=self.supplier_update_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_policy,
            clock=self.clock,
        )
        return [
            AttentionItem(
                case=health.case,
                sla=health.sla if health.sla_breached else None,
                missing_update=health.missing_update if health.update_overdue else None,
            )
            for health in healths
            if health.needs_attention
        ]


@dataclass(frozen=True)
class GetPortfolioSummary:
    """The Control Tower's portfolio view: every active
    case counted by the state it sits in and by supplier — counts over the
    same `CaseHealth` the Attention Queue filters, never a score a model or
    a formula assigned. Reuses `PO_CASE_READ`, tenant-wide like
    `GetAttentionQueue`: it reveals nothing a caller who can list every
    case could not already count for themselves.
    """

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext) -> PortfolioSummary:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)
        healths = await assess_active_cases(
            context,
            po_case_repo=self.po_case_repo,
            supplier_update_repo=self.supplier_update_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_policy,
            clock=self.clock,
        )
        return summarize_portfolio(healths)


def _approval_case_id(approval: PendingApprovalRecord) -> uuid.UUID | None:
    """The case an approval names — the `po_case_id` that
    `workflows.advance_case_graph` wrote into its payload when it raised it —
    or None when the payload carries no readable id."""
    try:
        return uuid.UUID(str(approval.payload.get("po_case_id")))
    except ValueError:
        return None


def _pending_case_approvals(
    approvals: Sequence[PendingApprovalRecord], cases: dict[uuid.UUID, POCase]
) -> tuple[PendingCaseApproval, ...]:
    """Each approval resolved to the caller's own case it names. One that
    names no readable case of the caller's is left out of the list (it still
    counts in the group's total), never guessed at."""
    resolved: list[PendingCaseApproval] = []
    for approval in approvals:
        case_id = _approval_case_id(approval)
        case = cases.get(case_id) if case_id is not None else None
        if case is None or approval.created_at is None:
            continue
        resolved.append(
            PendingCaseApproval(
                case=case,
                action=approval.approval_type.removeprefix(APPROVAL_TYPE_PREFIX),
                requested_at=approval.created_at,
            )
        )
    return tuple(resolved)


@dataclass(frozen=True)
class GetDailyBrief:
    """The daily management brief: what needs handling, grouped by signal and
    ordered by the tenant's own `SupplyChainBriefPolicy`. Every group is a
    deterministic signal (`domain.daily_brief`); the SLA and silence signals
    are the same `CaseHealth` the Attention Queue and the Control Tower use,
    so the brief cannot report a case those views do not.

    Reuses `PO_CASE_READ`, tenant-wide like `GetAttentionQueue`. Pending
    approvals are shown only to a caller who also holds the approval
    inbox's own `approvals.read`: the brief must not become a way to see
    approvals the inbox would refuse. Without it the group is absent and the
    brief says so (`approvals_visible=False`) rather than reading as "none
    pending"."""

    po_case_repo: POCaseRepositoryPort
    supplier_update_repo: SupplierUpdateRepositoryPort
    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainSLAPolicy
    platform_default_brief_policy: SupplyChainBriefPolicy
    pending_approvals: PendingApprovalsPort
    authz: AuthorizationPort
    clock: UtcClock

    async def handle(self, context: AccessContext) -> DailyBrief:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)
        healths = await assess_active_cases(
            context,
            po_case_repo=self.po_case_repo,
            supplier_update_repo=self.supplier_update_repo,
            policy_override_repo=self.policy_override_repo,
            platform_default_policy=self.platform_default_policy,
            clock=self.clock,
        )
        brief_policy = await _resolve_policy(
            context,
            self.policy_override_repo,
            policy_id=_BRIEF_POLICY_ID,
            schema=SupplyChainBriefPolicy,
            platform_default=self.platform_default_brief_policy,
        )
        now = self.clock.now()
        changed = await self.po_case_repo.list_latest_transitions_since(
            context, now - timedelta(hours=CHANGE_WINDOW_HOURS)
        )
        pending = await self._pending_approvals(context)

        # Changed and approval-bearing cases are often not active (a case
        # completed yesterday); read the ones the active set does not hold.
        cases = {health.case.id.value: health.case for health in healths}
        wanted = {case_id.value for case_id, _ in changed}
        if pending is not None:
            wanted |= {
                case_id
                for approval in pending[1]
                if (case_id := _approval_case_id(approval)) is not None
            }
        missing = [POCaseId(case_id) for case_id in wanted - cases.keys()]
        for case in await self.po_case_repo.get_many(context, missing):
            cases[case.id.value] = case

        return compose_brief(
            healths,
            recent_changes=[
                RecentChange(case=cases[case_id.value], transition=transition)
                for case_id, transition in changed
                if case_id.value in cases
            ],
            approvals=(
                None
                if pending is None
                else PendingApprovalsSeen(
                    total=pending[0], newest=_pending_case_approvals(pending[1], cases)
                )
            ),
            signal_order=brief_policy.signal_order,
            now=now,
        )

    async def _pending_approvals(
        self, context: AccessContext
    ) -> tuple[int, Sequence[PendingApprovalRecord]] | None:
        try:
            await self.authz.require(
                context=context, action=APPROVALS_READ, resource_type="approval_request"
            )
        except PermissionDeniedError:
            return None
        return await self.pending_approvals.list_pending_by_type_prefix(
            context, prefix=APPROVAL_TYPE_PREFIX, limit=_BRIEF_APPROVALS_READ
        )


@dataclass(frozen=True)
class GetBriefPolicy:
    """The effective brief order for the caller's own tenant — their own
    override if they have set one, the platform default otherwise."""

    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainBriefPolicy
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainBriefPolicy:
        await self.authz.require(
            context=context, action=BRIEF_POLICY_READ, resource_type=_BRIEF_POLICY_RESOURCE
        )
        return await _resolve_policy(
            context,
            self.policy_override_repo,
            policy_id=_BRIEF_POLICY_ID,
            schema=SupplyChainBriefPolicy,
            platform_default=self.platform_default_policy,
        )


@dataclass(frozen=True)
class SetBriefPolicyOverride:
    """Replaces the caller's tenant's own brief order, whole. `policy` arrives
    already validated by the route's request model: every signal listed
    exactly once, so an override can reorder the brief but never hide a
    group from it."""

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainBriefPolicy) -> None:
        await self.authz.require(
            context=context, action=BRIEF_POLICY_WRITE, resource_type=_BRIEF_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=_BRIEF_POLICY_ID,
            policy=policy,
            resource_type=_BRIEF_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


# How many rows a command-bar answer carries. The answer is a work surface,
# not the list itself: `has_more` offers the full, paged list instead.
_ANSWER_ROWS = 20


@dataclass(frozen=True, slots=True)
class CaseQueryAnswer:
    """What one question came to — every part of it decided by code.

    `plan.outcome` says which fields mean anything: `cases`/`has_more` for a
    LIST (and the matching cases for a PO_AMBIGUOUS), `opened` for an OPEN.
    `citations` are the question's own spans behind every field that was
    grounded, whatever the outcome. A refusal's two reasons are kept apart:
    `ignored` holds fields the model claimed that the question gave no
    grounds for; `unusable` holds fields the question did state but that
    this kind of answer cannot apply.
    """

    intent: CaseQueryKind
    plan: CaseQueryPlan
    citations: tuple[tuple[GroundedField, str], ...] = ()
    ignored: tuple[GroundedField, ...] = ()
    unusable: tuple[GroundedField, ...] = ()
    cases: tuple[POCase, ...] = ()
    has_more: bool = False
    opened: POCase | None = None


@dataclass(frozen=True)
class AnswerCaseQuery:
    """The command bar: a question in, a structured answer out.

    The model interprets and code decides, end to end: it reads the question into a typed
    intent (`workflows.case_query_understanding`); `domain.case_query`
    grounds it in the question and plans against the caller's real supplier
    names; the lookups run through `ListPOCases` and the repository, under
    the caller's own authorization and RLS. Nothing the model returned is
    used as an identifier until code has resolved it.

    Authorization comes first — before a token is spent. A model whose every
    answer failed the schema degrades to NOT_UNDERSTOOD; any other failure
    (a budget refusal, an unavailable provider) is raised as what it is,
    never dressed up as a misunderstanding.
    """

    po_case_repo: POCaseRepositoryPort
    list_cases: ListPOCases
    gateway: ModelGateway
    authz: AuthorizationPort
    ids: IdGenerator

    async def handle(self, context: AccessContext, question: str) -> CaseQueryAnswer:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)

        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_CASE_QUERY_WORKER_ID,
            worker_version=_WORKER_VERSION,
            channel="web",
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        try:
            intent = await understand_case_query(self.gateway, run_context, question)
        except ModelOutputInvalidError:
            return CaseQueryAnswer(
                intent=CaseQueryKind.UNSUPPORTED,
                plan=CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD),
            )

        grounded = ground(intent, question)
        # The caller's own names, read only for a list that names a supplier
        # — the one reading that resolves against them. The model never sees
        # them either way.
        known_suppliers = (
            await self.po_case_repo.list_supplier_names(context)
            if grounded.kind is CaseQueryKind.LIST_CASES and grounded.supplier_mention is not None
            else []
        )
        plan = plan_case_query(grounded, known_suppliers)
        answered = CaseQueryAnswer(
            intent=grounded.kind,
            plan=plan,
            citations=grounded.citations,
            ignored=ignored_fields(grounded),
            unusable=plan.unused,
        )

        if plan.outcome is CaseQueryOutcome.LIST:
            page = await self.list_cases.handle(
                context,
                POCaseListFilter(
                    state=plan.state,
                    supplier_name=plan.supplier_name,
                    active_only=plan.active_only,
                ),
                limit=_ANSWER_ROWS,
                cursor=None,
            )
            return replace(answered, cases=page.items, has_more=page.next_cursor is not None)

        if plan.outcome is CaseQueryOutcome.OPEN:
            assert plan.po_reference is not None  # OPEN always carries one
            matches = await self.po_case_repo.find_by_reference(context, plan.po_reference)
            if len(matches) == 1:
                # The stored reference, not the model's spelling of it.
                return replace(
                    answered,
                    plan=replace(plan, po_reference=matches[0].po_reference),
                    opened=matches[0],
                )
            return replace(
                answered,
                plan=replace(
                    plan,
                    outcome=(
                        CaseQueryOutcome.PO_AMBIGUOUS if matches else CaseQueryOutcome.PO_NOT_FOUND
                    ),
                    candidates=tuple(case.po_reference for case in matches),
                ),
                cases=tuple(matches),
            )

        return answered


@dataclass(frozen=True, slots=True)
class DailyBriefSummary:
    """A summary and the brief it was written from. They travel together:
    a sentence cites groups by key, and only the brief it was checked against
    can say what those keys hold."""

    brief: DailyBrief
    summary: BriefSummary


@dataclass(frozen=True)
class SummarizeDailyBrief:
    """The daily brief, summarized by a model and then checked by code.

    The brief comes from `GetDailyBrief` itself, so its authorization, its
    approvals gating and its composition are the one set the page already
    shows, and `PO_CASE_READ` is checked before a token is spent. A brief
    with no group is answered without a model call. Only
    `ModelOutputInvalidError` degrades to "unavailable": a budget refusal or
    an outage stays what it is. Every sentence the model writes goes through
    `domain.brief_summary.ground_summary` before anyone reads it."""

    get_daily_brief: GetDailyBrief
    gateway: ModelGateway
    ids: IdGenerator

    async def handle(self, context: AccessContext) -> DailyBriefSummary:
        brief = await self.get_daily_brief.handle(context)
        if not brief.groups:
            return DailyBriefSummary(
                brief=brief,
                summary=BriefSummary(status=BriefSummaryStatus.NOTHING_TO_SUMMARIZE),
            )
        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_BRIEF_SUMMARY_WORKER_ID,
            worker_version=_WORKER_VERSION,
            channel="web",
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        try:
            draft = await summarize_brief(self.gateway, run_context, brief)
        except ModelOutputInvalidError:
            return DailyBriefSummary(
                brief=brief, summary=BriefSummary(status=BriefSummaryStatus.UNAVAILABLE)
            )
        return DailyBriefSummary(brief=brief, summary=ground_summary(draft, brief))


@dataclass(frozen=True)
class GetActionDuties:
    """The effective step-to-duty mapping for the caller's own tenant — their
    own override if they have set one, the platform default otherwise."""

    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainActionDuties
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainActionDuties:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_ACTION_DUTIES_RESOURCE
        )
        return await _resolve_policy(
            context,
            self.policy_override_repo,
            policy_id=_ACTION_DUTIES_POLICY_ID,
            schema=SupplyChainActionDuties,
            platform_default=self.platform_default_duties,
        )


@dataclass(frozen=True)
class SetActionDutiesOverride:
    """Replaces the caller's tenant's own step-to-duty mapping, whole. `duties`
    arrives validated by the route's request model: every action has a duty,
    so an override can move a step between departments but never leave one
    that nobody, or anybody, may take."""

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, duties: SupplyChainActionDuties) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_ACTION_DUTIES_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=_ACTION_DUTIES_POLICY_ID,
            policy=duties,
            resource_type=_ACTION_DUTIES_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


async def resolve_product_action_duties(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default_duties: SupplyChainProductActionDuties,
) -> SupplyChainProductActionDuties:
    """The tenant's own product step-to-duty mapping if it has set one, the
    platform's otherwise: what a product step is authorized against and what
    `GetProductActionDuties` shows, from one place."""
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=PRODUCT_ACTION_DUTIES_POLICY_ID,
        schema=SupplyChainProductActionDuties,
        platform_default=platform_default_duties,
    )


async def resolve_product_approvals(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainProductApprovals,
) -> SupplyChainProductApprovals:
    """Who may decide each product-case approval for the caller's tenant: its
    own override if it has one, the platform's otherwise. Read only where an
    approval is raised; deciding reads the stamp the approval carries."""
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=PRODUCT_APPROVALS_POLICY_ID,
        schema=SupplyChainProductApprovals,
        platform_default=platform_default,
    )


@dataclass(frozen=True)
class GetProductActionDuties:
    """The effective product step-to-duty mapping for the caller's own tenant.
    Reuses the action-duties scopes: who may read or set one duty mapping may
    read or set the other."""

    policy_override_repo: PolicyOverridePort
    platform_default_duties: SupplyChainProductActionDuties
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainProductActionDuties:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_ACTION_DUTIES_RESOURCE
        )
        return await resolve_product_action_duties(
            context, self.policy_override_repo, self.platform_default_duties
        )


@dataclass(frozen=True)
class SetProductActionDutiesOverride:
    """Replaces the caller's tenant's own product step-to-duty mapping, whole;
    every step must keep a duty (the schema refuses otherwise)."""

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, duties: SupplyChainProductActionDuties) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_ACTION_DUTIES_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=PRODUCT_ACTION_DUTIES_POLICY_ID,
            policy=duties,
            resource_type="product_action_duties",
            ids=self.ids,
            clock=self.clock,
        )


# -- follow-ups -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FollowUpView:
    """An open follow-up as a caller sees it: `mine` when the caller holds
    one of the scopes it was handed to, which is also who may close it."""

    record: FollowUpRecord
    mine: bool


@dataclass(frozen=True)
class ListFollowUps:
    """The tenant's open follow-ups, newest first. Reuses `PO_CASE_READ`: a
    follow-up says nothing a caller who can read the cases could not see."""

    follow_up_repo: FollowUpRepositoryPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> list[FollowUpView]:
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type=_RESOURCE)
        return [
            FollowUpView(record=record, mine=bool(record.recipient_scopes & context.scopes))
            for record in await self.follow_up_repo.list_open(context)
        ]


@dataclass(frozen=True)
class CloseFollowUp:
    """A person marks a follow-up done. Only someone it was handed to may:
    the recipient scopes stamped on it when it opened decide, not the
    current policy. The audit event commits with the change."""

    follow_up_repo: FollowUpRepositoryPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, follow_up_id: uuid.UUID, note: str | None
    ) -> None:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=_FOLLOW_UP_RESOURCE,
            resource_id=str(follow_up_id),
        )
        record = await self.follow_up_repo.get(context, follow_up_id)
        if record is None:
            raise NotFoundError("follow-up not found", details={"follow_up_id": str(follow_up_id)})
        if not record.recipient_scopes & context.scopes:
            raise PermissionDeniedError(
                "only someone this follow-up was handed to may close it",
                details={"follow_up_id": str(follow_up_id)},
            )
        cleaned = note.strip() if note else None
        closed = await self.follow_up_repo.close_done(
            context,
            follow_up_id,
            note=cleaned or None,
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action="supply_chain.follow_up.done",
                resource_type=_FOLLOW_UP_RESOURCE,
                resource_id=str(follow_up_id),
                occurred_at=self.clock.now(),
                details={"po_case_id": str(record.po_case_id), "kind": record.kind.value},
            ),
        )
        if not closed:
            raise DomainError(
                "this follow-up is already closed", details={"follow_up_id": str(follow_up_id)}
            )


async def resolve_follow_up_policy(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default_policy: SupplyChainFollowUpPolicy,
) -> SupplyChainFollowUpPolicy:
    """The tenant's own routing if it has set one, the platform's otherwise:
    what the sweep stamps on a follow-up and what `GetFollowUpPolicy` shows."""
    return await _resolve_policy(
        context,
        policy_override_repo,
        policy_id=FOLLOW_UP_POLICY_ID,
        schema=SupplyChainFollowUpPolicy,
        platform_default=platform_default_policy,
    )


@dataclass(frozen=True)
class GetFollowUpPolicy:
    """Who is handed each kind of follow-up in the caller's own tenant."""

    policy_override_repo: PolicyOverridePort
    platform_default_policy: SupplyChainFollowUpPolicy
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainFollowUpPolicy:
        await self.authz.require(
            context=context, action=FOLLOW_UP_POLICY_READ, resource_type=_FOLLOW_UP_POLICY_RESOURCE
        )
        return await resolve_follow_up_policy(
            context, self.policy_override_repo, self.platform_default_policy
        )


@dataclass(frozen=True)
class SetFollowUpPolicyOverride:
    """Replaces the caller's tenant's own routing, whole. Every kind must
    still reach someone (the policy schema refuses otherwise). Follow-ups
    already open keep the recipients they were stamped with."""

    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainFollowUpPolicy) -> None:
        await self.authz.require(
            context=context, action=FOLLOW_UP_POLICY_WRITE, resource_type=_FOLLOW_UP_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=FOLLOW_UP_POLICY_ID,
            policy=policy,
            resource_type=_FOLLOW_UP_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )
