"""The pre-production test's checklist and record (step 12, R&D; ticket
ai-automation/17, item 2), built the way a sample round's are (ticket
ai-automation/09).

- **What is measured** (`PreProductionTestSources`): the tenant's sample criteria
  for the product's Category (the PO case's product case; the default list
  when it has none), R&D's newest value of each in THIS attempt
  (`domain.pre_production_test.attempt_of`), and the one comparison every
  reader uses (`domain.sample_evaluation.judge`).
- **`RecordPreProductionMeasurement`**: the scope of the duty that passes the
  test (R&D's under the tenant's PO step-to-duty policy), checked before
  anything is read, as the step itself checks it; the case in the caller's
  workspace, in `pre_production`, its sample received and its test not
  passed; the criterion one of the list; the value one the criterion takes.
  One append-only row; a correction is a newer row.
- **`GetPreProductionChecklist`** (`supply_chain.po_case.read`): the list, the
  values, the verdicts, and code's suggestion beside the empty pass / fail.
- **The record** (`report_values`, `report_evidence`): what the packaging
  lane drafts once every criterion is measured: code's criteria table, the
  model's notes only where they check out, the date, the tester and the
  conclusion left for a person (the conclusion is the step R&D takes).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dw_kernel.errors import DomainError, NotFoundError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import SupplyChainActionDuties
from dw_supply_chain.application.commercial import allows
from dw_supply_chain.application.document_drafts import FieldInput
from dw_supply_chain.application.handlers import (
    PO_CASE_READ,
    duty_scope,
    resolve_action_duties,
)
from dw_supply_chain.application.po_case_audit import PO_CASE_RESOURCE, po_case_audit
from dw_supply_chain.application.ports import PackagingDesignRepositoryPort
from dw_supply_chain.application.purchase_orders import POCaseStorePort
from dw_supply_chain.application.step_preparation import CaseReadPort
from dw_supply_chain.domain.document_draft import DocumentDraft, field_value
from dw_supply_chain.domain.grounded_writing import EvidenceItem, KeptSentence
from dw_supply_chain.domain.packaging_design import PackagingAction, PackagingDesign
from dw_supply_chain.domain.po_case import CaseState, POCase, POCaseId
from dw_supply_chain.domain.pre_production_test import attempt_of, suggested_test_action
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCaseId
from dw_supply_chain.domain.sample_criteria import SampleCriterion, Verdict
from dw_supply_chain.domain.sample_evaluation import (
    CriterionResult,
    Measurement,
    criteria_rows,
    evidence,
    judge,
    measurements_digest,
    notes_text,
)
from dw_supply_chain.sample_criteria_policy import (
    SupplyChainSampleCriteria,
    resolve_sample_criteria,
)

MEASUREMENT_RECORDED = "pre_production_measurement.recorded"
_RESOURCE = "pre_production_measurement"
_NOTE_MAX = 500


@dataclass(frozen=True, slots=True)
class NewPreProductionMeasurement:
    id: uuid.UUID
    po_case_id: uuid.UUID
    attempt: int
    criterion: str
    value: str
    note: str | None


class PreProductionMeasurementsPort(Protocol):
    """What R&D measured on a PO case's pre-production sample, one attempt,
    under the caller's tenant and workspace (RLS). `Measurement.sample_round`
    carries the attempt."""

    async def for_attempt(
        self, context: AccessContext, po_case_id: uuid.UUID, attempt: int
    ) -> list[Measurement]: ...


class PreProductionMeasurementStorePort(PreProductionMeasurementsPort, Protocol):
    async def add(
        self, context: AccessContext, measurement: NewPreProductionMeasurement, *, audit: AuditEvent
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class PreProductionResults:
    attempt: int
    results: tuple[CriterionResult, ...]

    @property
    def suggestion(self) -> PackagingAction | None:
        return suggested_test_action(self.results)

    @property
    def complete(self) -> bool:
        """Every criterion measured: the record can be drafted."""
        return bool(self.results) and all(r.verdict is not Verdict.UNMEASURED for r in self.results)

    @property
    def rows(self) -> list[dict[str, str | None]]:
        return criteria_rows(self.results)

    @property
    def digest(self) -> str:
        """Which results these are, short enough for a notice's key."""
        raw = f"{self.attempt}:{measurements_digest(self.results)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def test_open(design: PackagingDesign | None) -> bool:
    """The sample is in and the test is not passed: values may be entered and
    the test taken (the same condition `PackagingDesign` opens the step on)."""
    return design is not None and design.test_open


@dataclass(frozen=True)
class PreProductionTestSources:
    measurements: PreProductionMeasurementsPort
    designs: PackagingDesignRepositoryPort
    product_cases: CaseReadPort
    policy_override_repo: PolicyOverridePort
    platform_default_criteria: SupplyChainSampleCriteria

    async def criteria(self, context: AccessContext, case: POCase) -> tuple[SampleCriterion, ...]:
        policy = await resolve_sample_criteria(
            context, self.policy_override_repo, self.platform_default_criteria
        )
        category = ""
        if case.product_dev_case_id is not None:
            product = await self.product_cases.get(
                context, ProductDevelopmentCaseId(case.product_dev_case_id)
            )
            if product is not None and product.workspace_id.value == context.workspace_id:
                category = product.category
        return policy.for_category(category)

    async def results(self, context: AccessContext, case: POCase) -> PreProductionResults:
        attempt = attempt_of(await self.designs.history(context, case.id.value))
        return PreProductionResults(
            attempt=attempt,
            results=tuple(
                judge(
                    await self.criteria(context, case),
                    await self.measurements.for_attempt(context, case.id.value, attempt),
                    attempt,
                )
            ),
        )


async def _case_in_workspace(
    cases: POCaseStorePort, context: AccessContext, case_id: POCaseId
) -> POCase:
    case = await cases.get(context, case_id)
    if (
        case is None
        or case.tenant_id.value != context.tenant_id
        or case.workspace_id.value != context.workspace_id
    ):
        raise NotFoundError("PO case not found", details={"case_id": str(case_id)})
    return case


@dataclass(frozen=True)
class RecordPreProductionMeasurement:
    cases: POCaseStorePort
    store: PreProductionMeasurementStorePort
    test: PreProductionTestSources
    authz: AuthorizationPort
    platform_default_duties: SupplyChainActionDuties
    ids: IdGenerator
    clock: UtcClock

    async def scope(self, context: AccessContext) -> str:
        """The scope of the duty that passes the test: who takes the step
        enters what it is decided on."""
        duties = await resolve_action_duties(
            context, self.test.policy_override_repo, self.platform_default_duties
        )
        return duty_scope(duties.duty_for(PackagingAction.PASS_PRE_PRODUCTION_TEST))

    async def handle(
        self,
        context: AccessContext,
        case_id: POCaseId,
        *,
        criterion: str,
        value: str,
        note: str | None,
    ) -> PreProductionResults:
        await self.authz.require(
            context=context,
            action=await self.scope(context),
            resource_type=PO_CASE_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.cases, context, case_id)
        design = await self.test.designs.get(context, case.id.value)
        if case.state is not CaseState.PRE_PRODUCTION or not test_open(design):
            raise DomainError(
                "chỉ nhập số đo test trước SX khi đã nhận mẫu và test chưa đạt",
                details={"state": case.state.value},
            )
        found = next(
            (c for c in await self.test.criteria(context, case) if c.key == criterion), None
        )
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
        attempt = attempt_of(await self.test.designs.history(context, case.id.value))
        await self.store.add(
            context,
            NewPreProductionMeasurement(
                id=self.ids.new_uuid(),
                po_case_id=case.id.value,
                attempt=attempt,
                criterion=criterion,
                value=canonical,
                note=text,
            ),
            audit=po_case_audit(
                context,
                self.ids,
                self.clock,
                case.id,
                MEASUREMENT_RECORDED,
                {"attempt": attempt, "criterion": criterion, "value": canonical},
            ),
        )
        return await self.test.results(context, case)


@dataclass(frozen=True, slots=True)
class PreProductionChecklist:
    case: POCase
    results: PreProductionResults
    # The sample is in and the test not passed.
    open: bool
    # The caller may enter values now (open, and they hold the duty).
    can_record: bool


@dataclass(frozen=True)
class GetPreProductionChecklist:
    record: RecordPreProductionMeasurement
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, case_id: POCaseId) -> PreProductionChecklist:
        await self.authz.require(
            context=context,
            action=PO_CASE_READ,
            resource_type=PO_CASE_RESOURCE,
            resource_id=str(case_id),
        )
        case = await _case_in_workspace(self.record.cases, context, case_id)
        design = await self.record.test.designs.get(context, case.id.value)
        is_open = case.state is CaseState.PRE_PRODUCTION and test_open(design)
        return PreProductionChecklist(
            case=case,
            results=await self.record.test.results(context, case),
            open=is_open,
            can_record=is_open
            and await allows(self.authz, context, await self.record.scope(context), _RESOURCE),
        )


# ----------------------------------------------------------------- record --


def report_case_facts(case: POCase, attempt: int) -> str:
    """What the model may cite of the case: never a price."""
    return (
        f"PO {case.po_reference or 'chưa có số'}; nhà cung cấp {case.supplier_name}; "
        f"test trước sản xuất lần {attempt}"
    )


def report_evidence(case: POCase, results: PreProductionResults) -> list[EvidenceItem]:
    return evidence(report_case_facts(case, results.attempt), results.results, ())


def report_values(
    case: POCase,
    results: PreProductionResults,
    attributes: Mapping[str, Any] | None,
    notes: Sequence[KeptSentence],
) -> dict[str, FieldInput]:
    """The record's fields: the case's own words, code's criteria table, and
    the notes that checked out with what they cite. The date, the tester and
    the conclusion stay a person's."""
    values = {
        "supplier_name": FieldInput(value=case.supplier_name),
        "test_attempt": FieldInput(value=str(results.attempt)),
        "criteria": FieldInput(value=results.rows),
    }
    if case.po_reference is not None:
        values["po_reference"] = FieldInput(value=case.po_reference)
    product = (attributes or {}).get("product_name")
    if isinstance(product, str) and product.strip():
        values["product_name"] = FieldInput(value=product.strip())
    text = notes_text(notes)
    if text is not None:
        values["notes"] = FieldInput(
            value=text, cites=tuple(dict.fromkeys(c for n in notes for c in n.cites))
        )
    return values


def drafted_for(draft: DocumentDraft, results: PreProductionResults) -> bool:
    """This draft records exactly these results: same attempt, same rows."""
    attempt = field_value(draft.fields, "test_attempt")
    rows = field_value(draft.fields, "criteria")
    return bool(attempt == str(results.attempt) and rows == results.rows)


__all__ = [
    "MEASUREMENT_RECORDED",
    "GetPreProductionChecklist",
    "NewPreProductionMeasurement",
    "PreProductionChecklist",
    "PreProductionMeasurementStorePort",
    "PreProductionMeasurementsPort",
    "PreProductionResults",
    "PreProductionTestSources",
    "RecordPreProductionMeasurement",
    "drafted_for",
    "report_evidence",
    "report_values",
    "test_open",
]
