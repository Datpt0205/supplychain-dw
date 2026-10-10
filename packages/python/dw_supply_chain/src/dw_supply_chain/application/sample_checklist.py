"""A sample round's checklist: what R&D measures, what it measured, and what
code makes of it (steps 3-5; ticket ai-automation/09).

- **`GetSampleChecklist`** (`supply_chain.product_case.read`): the criteria of
  the case's Category, the newest value of each this round, and each verdict
  (`domain.sample_criteria`), the same comparison the record's table reads.
- **`RecordMeasurement`**: the product write and the scope of the duty that
  passes a sample (R&D's), checked before anything is read; the case in the
  caller's workspace and in `sample_testing`; the criterion one of the case's
  Category; the value one the criterion takes (a number, or pass / fail). One
  append-only row; a correction is a newer row. A value entered after a
  proposal was raised moves the proposal's subject, so its approval is
  superseded and the round prepared again.
- **`GetSampleCriteriaPolicy`** / **`SetSampleCriteriaPolicyOverride`**: the
  tenant's criteria, read and replaced whole (`action_duties.read|write`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.commercial import allows
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    _put_policy_override,
    duty_scope,
    resolve_product_action_duties,
)
from dw_supply_chain.application.step_preparation import (
    CaseReadPort,
    MeasurementsPort,
    SamplePreparation,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.domain.sample_evaluation import CriterionResult
from dw_supply_chain.product_action_duties import SupplyChainProductActionDuties
from dw_supply_chain.sample_criteria_policy import (
    SAMPLE_CRITERIA_POLICY_ID,
    SupplyChainSampleCriteria,
    resolve_sample_criteria,
)

_RESOURCE = "sample_measurement"
_POLICY_RESOURCE = "sample_criteria_policy"
MEASUREMENT_RECORDED = "supply_chain.sample_measurement.recorded"
_NOTE_MAX = 500


@dataclass(frozen=True, slots=True)
class NewMeasurement:
    id: uuid.UUID
    case_id: uuid.UUID
    sample_round: int
    criterion: str
    value: str
    note: str | None


class MeasurementStorePort(MeasurementsPort, Protocol):
    async def add(
        self, context: AccessContext, measurement: NewMeasurement, *, audit: AuditEvent
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class SampleChecklist:
    case: ProductDevelopmentCase
    results: tuple[CriterionResult, ...]
    # The caller may enter values now (the case is being tested and they
    # hold the duty).
    can_record: bool


async def _case_in_workspace(
    cases: CaseReadPort, context: AccessContext, case_id: uuid.UUID
) -> ProductDevelopmentCase:
    case = await cases.get(context, ProductDevelopmentCaseId(case_id))
    if case is None or case.workspace_id.value != context.workspace_id:
        raise NotFoundError("product case not found", details={"case_id": str(case_id)})
    return case


@dataclass(frozen=True)
class RecordMeasurement:
    cases: CaseReadPort
    store: MeasurementStorePort
    # The criteria and the one comparison the preparation reads too.
    sample: SamplePreparation
    authz: AuthorizationPort
    platform_default_duties: SupplyChainProductActionDuties
    ids: IdGenerator
    clock: UtcClock

    async def scopes(self, context: AccessContext) -> frozenset[str]:
        """The write and the scope of the duty that passes a sample."""
        duties = await resolve_product_action_duties(
            context, self.sample.policy_override_repo, self.platform_default_duties
        )
        return frozenset(
            {PRODUCT_CASE_WRITE, duty_scope(duties.duty_for(ProductAction.PASS_SAMPLE))}
        )

    async def handle(
        self,
        context: AccessContext,
        case_id: uuid.UUID,
        *,
        criterion: str,
        value: str,
        note: str | None,
    ) -> list[CriterionResult]:
        for scope in sorted(await self.scopes(context)):
            await self.authz.require(context=context, action=scope, resource_type=_RESOURCE)
        case = await _case_in_workspace(self.cases, context, case_id)
        if case.state is not ProductDevState.SAMPLE_TESTING:
            raise DomainError(
                "chỉ nhập số đo khi hồ sơ đang test mẫu", details={"state": case.state.value}
            )
        criteria = await resolve_sample_criteria(
            context, self.sample.policy_override_repo, self.sample.platform_default_criteria
        )
        found = next((c for c in criteria.for_category(case.category) if c.key == criterion), None)
        if found is None:
            raise DomainError(
                "tiêu chí không thuộc nhóm sản phẩm của hồ sơ", details={"criterion": criterion}
            )
        canonical = found.accepts(value)
        if canonical is None:
            raise DomainError(
                "giá trị không hợp lệ cho tiêu chí này",
                details={"criterion": criterion, "kind": found.kind.value},
            )
        text = (note or "").strip() or None
        if text is not None and len(text) > _NOTE_MAX:
            raise DomainError(f"ghi chú tối đa {_NOTE_MAX} ký tự", details={"field": "note"})
        measurement = NewMeasurement(
            id=self.ids.new_uuid(),
            case_id=case.id.value,
            sample_round=case.sample_round,
            criterion=criterion,
            value=canonical,
            note=text,
        )
        await self.store.add(
            context,
            measurement,
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=MEASUREMENT_RECORDED,
                resource_type="product_dev_case",
                resource_id=str(case.id),
                occurred_at=self.clock.now(),
                details={
                    "sample_round": case.sample_round,
                    "criterion": criterion,
                    "value": canonical,
                },
            ),
        )
        return await self.sample.results(context, case)


@dataclass(frozen=True)
class GetSampleChecklist:
    cases: CaseReadPort
    record: RecordMeasurement
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: uuid.UUID) -> SampleChecklist:
        await self.authz.require(context=context, action=PRODUCT_CASE_READ, resource_type=_RESOURCE)
        case = await _case_in_workspace(self.cases, context, case_id)
        results = await self.record.sample.results(context, case)
        can_record = case.state is ProductDevState.SAMPLE_TESTING and all(
            [
                await allows(self.authz, context, scope, _RESOURCE)
                for scope in await self.record.scopes(context)
            ]
        )
        return SampleChecklist(case=case, results=tuple(results), can_record=can_record)


@dataclass(frozen=True)
class GetSampleCriteriaPolicy:
    policy_override_repo: PolicyOverridePort
    platform_default_criteria: SupplyChainSampleCriteria
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainSampleCriteria:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_POLICY_RESOURCE
        )
        return await resolve_sample_criteria(
            context, self.policy_override_repo, self.platform_default_criteria
        )


@dataclass(frozen=True)
class SetSampleCriteriaPolicyOverride:
    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainSampleCriteria) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=SAMPLE_CRITERIA_POLICY_ID,
            policy=policy,
            resource_type=_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


__all__ = [
    "GetSampleChecklist",
    "GetSampleCriteriaPolicy",
    "MeasurementStorePort",
    "NewMeasurement",
    "RecordMeasurement",
    "SampleChecklist",
    "SetSampleCriteriaPolicyOverride",
]
