"""Unit: steps 3-5 measured, worded and decided (ticket ai-automation/09).

The real `PrepareStep`, `ApplyStepProposal`, `StepProposalSubject` and
`RecordMeasurement` over the in-memory world of `testing.step_preparation`,
the shipped prompt rendered through the real registry, with Elmich's own step
(`SAMPLE_ROUND`, read from its override file): biên bản and phiếu drafted from
R&D's measurements, three outcomes chosen in the empty conclusion.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_agent_runtime.ports import ModelOutputInvalidError
from dw_kernel.errors import DomainError, NotFoundError, PermissionDeniedError
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import SUBJECT_VERSION_KEY
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import PRODUCT_CASE_WRITE, duty_scope
from dw_supply_chain.application.sample_checklist import RecordMeasurement
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.domain.sample_criteria import CriterionKind, SampleCriterion
from dw_supply_chain.domain.sample_evaluation import EvaluationWriting, RequirementWriting
from dw_supply_chain.policy_files import PRODUCT_ACTION_DUTIES_POLICY_FILE
from dw_supply_chain.product_action_duties import load_supply_chain_product_action_duties
from dw_supply_chain.sample_criteria_policy import SupplyChainSampleCriteria
from dw_supply_chain.step_preparation_policy import PreparedStep
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.step_preparation import NOW, SAMPLE_ROUND, StepWorld

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")
CRITERIA = SupplyChainSampleCriteria(
    schema_version="1.0",
    policy_id="supply_chain_sample_criteria",
    policy_version="1.0.0",
    default=(
        SampleCriterion(
            key="base_thickness",
            label="Độ dày đáy",
            kind=CriterionKind.NUMBER,
            unit="mm",
            min=Decimal(3),
        ),
        SampleCriterion(key="appearance", label="Ngoại quan", kind=CriterionKind.CHECK),
    ),
)
WRITING = EvaluationWriting(
    notes=[
        CitedSentence(
            text="Độ dày đáy đo được 2.5 mm, thấp hơn chuẩn 3 mm.",
            cites=["criterion:base_thickness"],
        ),
        CitedSentence(text="Mẫu đạt 99% yêu cầu khách hàng.", cites=["case"]),
    ],
    requirements=[
        RequirementWriting(
            criterion="base_thickness",
            text="Tăng độ dày đáy lên ít nhất 3 mm.",
            cites=["criterion:base_thickness"],
        ),
        RequirementWriting(
            criterion="appearance", text="Đánh bóng lại bề mặt.", cites=["criterion:appearance"]
        ),
    ],
)


def _world(answer: object = WRITING) -> StepWorld:
    return StepWorld(
        criteria=CRITERIA,
        gateway=None if answer is None else ScriptedGateway(PROMPTS, answer=answer),  # type: ignore[arg-type]
    )


def _prepare(
    world: StepWorld, *, thickness: str | None = "2.5", appearance: str | None = "pass"
) -> tuple[Any, Any]:
    case, entered = world.add_case(ProductDevState.SAMPLE_TESTING)
    if thickness is not None:
        world.measure(case, "base_thickness", thickness)
    if appearance is not None:
        world.measure(case, "appearance", appearance)
    lane = world.context(world.lane_context(case).principal_id)
    prepared = asyncio.run(
        world.preparer().prepare(lane, world.request(case, entered, SAMPLE_ROUND))
    )
    return case, prepared


def _draft(world: StepWorld, doc_type: DocumentType) -> Mapping[str, Any]:
    found = [d for d in world.drafts.rows if d.doc_type is doc_type]
    assert found, doc_type
    return found[-1].fields


def _value(fields: Mapping[str, Any], name: str) -> Any:
    entry = fields.get(name)
    return entry.get("value") if isinstance(entry, Mapping) else None


def test_the_record_s_table_is_code_s_and_only_notes_that_check_out_are_kept() -> None:
    world = _world()
    _, prepared = _prepare(world)
    assert prepared.outcome.value == "proposed"
    record = _draft(world, DocumentType.SAMPLE_EVALUATION)
    assert _value(record, "criteria") == [
        {
            "criterion": "Độ dày đáy",
            "standard": "≥ 3 mm",
            "measured": "2.5 mm",
            "result": "Không đạt",
        },
        {"criterion": "Ngoại quan", "standard": "Đạt", "measured": "Đạt", "result": "Đạt"},
    ]
    notes = _value(record, "notes")
    assert "2.5 mm" in notes and "99%" not in notes
    assert record["notes"]["source"] == {"ai_written": True, "cites": ["criterion:base_thickness"]}
    # The conclusion and the date are the person's, whatever the model wrote.
    assert _value(record, "conclusion") is None and _value(record, "evaluated_on") is None


def test_the_request_has_an_item_per_failed_criterion_and_only_its_own_requirement() -> None:
    world = _world()
    _prepare(world)
    request = _draft(world, DocumentType.SAMPLE_REVISION_REQUEST)
    assert _value(request, "items") == [
        {
            "criterion": "Độ dày đáy",
            "finding": "Đo được 2.5 mm, chuẩn ≥ 3 mm",
            "requirement": "Tăng độ dày đáy lên ít nhất 3 mm.",
        }
    ]
    assert _value(request, "requested_on") == NOW.date().isoformat()


def test_a_requirement_with_an_invented_threshold_or_no_citation_leaves_a_gap() -> None:
    world = _world(
        EvaluationWriting(
            requirements=[
                RequirementWriting(
                    criterion="base_thickness",
                    text="Tăng độ dày đáy lên 4 mm.",
                    cites=["criterion:base_thickness"],
                ),
                RequirementWriting(criterion="base_thickness", text="Làm dày đáy hơn.", cites=[]),
            ]
        )
    )
    _prepare(world)
    [draft] = [d for d in world.drafts.rows if d.doc_type is DocumentType.SAMPLE_REVISION_REQUEST]
    assert _value(draft.fields, "items")[0]["requirement"] is None
    assert "items[0].requirement" in draft.gaps


def test_no_model_writing_still_drafts_code_s_rows_and_names_the_gaps() -> None:
    for world in (_world(None), _world(ModelOutputInvalidError("bad"))):
        _, prepared = _prepare(world)
        assert prepared.outcome.value == "proposed"
        record = _draft(world, DocumentType.SAMPLE_EVALUATION)
        assert _value(record, "notes") is None
        assert len(_value(record, "criteria")) == 2


def test_the_suggestion_is_code_s_comparison_beside_an_empty_choice() -> None:
    world = _world()
    _, prepared = _prepare(world)
    suggestion = prepared.payload["suggestions"]["conclusion"]
    assert suggestion["value"] == "Cần chỉnh sửa" and "Độ dày đáy" in suggestion["quote"]
    world = _world()
    _, prepared = _prepare(world, thickness="3.2")
    assert prepared.payload["suggestions"]["conclusion"]["value"] == "Đạt"
    world = _world()
    _, prepared = _prepare(world, thickness="3.2", appearance=None)
    assert "conclusion" not in prepared.payload["suggestions"]
    assert "criterion_unmeasured" in {f["code"] for f in prepared.payload["findings"]}


def _decide(world: StepWorld, prepared: Any, choice: str, comment: str = "Theo số đo") -> str:
    return asyncio.run(
        world.applier().apply(
            world.context(),
            prepared.payload,
            approved=True,
            comment=comment,
            typed_input={"evaluated_on": "2026-10-08", "conclusion": choice},
            run_id=None,
        )
    )


def test_revise_sends_the_request_with_the_comment_as_its_reason() -> None:
    world = _world()
    case, prepared = _prepare(world)
    assert _decide(world, prepared, "revise", "Đáy mỏng hơn chuẩn") == "request_revision"
    stored = world.cases.cases[case.id.value]
    assert stored.state is ProductDevState.REVISION_REQUESTED
    decisions = [d for d, _, _ in world.drafts.decisions.values()]
    assert decisions.count(DraftDecision.CONFIRMED) == 2
    record = [d for d in world.drafts.rows if d.doc_type is DocumentType.SAMPLE_EVALUATION][-1]
    assert _value(record.fields, "conclusion") == "Cần chỉnh sửa"
    assert {d.doc_type for d in world.documents.rows} >= {
        DocumentType.SAMPLE_EVALUATION,
        DocumentType.SAMPLE_REVISION_REQUEST,
    }


def test_pass_closes_the_unused_request_and_moves_to_bgd() -> None:
    world = _world()
    case, prepared = _prepare(world, thickness="3.5")
    assert _decide(world, prepared, "pass") == "pass_sample"
    assert world.cases.cases[case.id.value].state is ProductDevState.PENDING_BOD_REVIEW
    by_type = {
        d.doc_type: world.drafts.decisions[d.id][0]
        for d in world.drafts.rows
        if d.id in world.drafts.decisions
    }
    assert by_type[DocumentType.SAMPLE_REVISION_REQUEST] is DraftDecision.REJECTED
    assert DocumentType.SAMPLE_REVISION_REQUEST not in {d.doc_type for d in world.documents.rows}


def test_a_choice_the_step_does_not_offer_is_refused_and_nothing_moves() -> None:
    world = _world()
    case, prepared = _prepare(world)
    with pytest.raises(DomainError):
        _decide(world, prepared, "Đạt")
    assert world.cases.cases[case.id.value].state is ProductDevState.SAMPLE_TESTING


def test_a_value_entered_after_the_proposal_moves_its_subject() -> None:
    world = _world()
    case, prepared = _prepare(world)
    subject = world.subject()
    context = world.context()
    assert (
        asyncio.run(subject.current(context, prepared.payload))
        == prepared.payload[SUBJECT_VERSION_KEY]
    )
    world.measure(case, "base_thickness", "3.1")
    assert (
        asyncio.run(subject.current(context, prepared.payload))
        != prepared.payload[SUBJECT_VERSION_KEY]
    )


def test_the_next_round_checks_each_item_of_the_last_request() -> None:
    world = _world()
    case, prepared = _prepare(world)
    _decide(world, prepared, "revise")
    stored = world.cases.cases[case.id.value]
    # The revised sample arrives: round 2, measured again.
    from dw_supply_chain.domain.product_development_case import (
        ProductAction,
        ProductActionInput,
        apply_product_action,
    )

    apply_product_action(
        stored,
        action=ProductAction.RECEIVE_REVISED_SAMPLE,
        given=ProductActionInput(actor_id=uuid.uuid4()),
    )
    stored.pop_pending_steps()
    entered = world.cases.enter(stored, NOW)
    world.measure(stored, "base_thickness", "2.8", sample_round=2)
    lane = world.context(world.lane_context(stored).principal_id)
    again = asyncio.run(
        world.preparer().prepare(lane, world.request(stored, entered, SAMPLE_ROUND))
    )
    assert again.outcome.value == "proposed", again.reason
    codes = {(f["code"], f["subject"]) for f in again.payload["findings"]}
    assert ("revision_item_open", "Độ dày đáy") in codes


def test_a_case_of_another_workspace_costs_no_model_call() -> None:
    world = _world()
    case, entered = world.add_case(ProductDevState.SAMPLE_TESTING, workspace=uuid.uuid4())
    world.measure(case, "base_thickness", "2.5")
    prepared = asyncio.run(
        world.preparer().prepare(world.context(), world.request(case, entered, SAMPLE_ROUND))
    )
    assert prepared.outcome.value == "not_prepared"
    assert world.gateway.sent == []


def test_outcomes_name_a_field_steps_and_papers_the_step_can_take() -> None:
    raw = SAMPLE_ROUND.model_dump(mode="json")
    with pytest.raises(ValueError, match="result field"):
        PreparedStep.model_validate({**raw, "outcome_field": "notes"})
    with pytest.raises(ValueError, match="cannot take"):
        PreparedStep.model_validate(
            {**raw, "outcomes": {**raw["outcomes"], "x": {"action": "bod_approve", "label": "x"}}}
        )
    without_request = {
        **raw,
        "drafts": [d for d in raw["drafts"] if d["doc_type"] != "sample_revision_request"],
    }
    with pytest.raises(ValueError, match="sample_revision_request"):
        PreparedStep.model_validate(without_request)


# ---------------------------------------------------------- measurements --

RND = duty_scope(CaseDuty.RND)
DUTIES = load_supply_chain_product_action_duties(
    REPO_ROOT / "configs" / "policies" / PRODUCT_ACTION_DUTIES_POLICY_FILE
)


def _recorder(world: StepWorld) -> RecordMeasurement:
    return RecordMeasurement(
        cases=world.cases,
        store=world.measurements,
        sample=world.sample(),
        authz=ScopeAuthorizationService(),
        platform_default_duties=DUTIES,
        ids=Uuid4Generator(),
        clock=world.clock,
    )


def test_r_and_d_records_a_value_the_criterion_takes_on_a_case_being_tested() -> None:
    world = _world()
    case, _ = world.add_case(ProductDevState.SAMPLE_TESTING)
    rnd = world.context(scopes=frozenset({PRODUCT_CASE_WRITE, RND}))
    results = asyncio.run(
        _recorder(world).handle(
            rnd, case.id.value, criterion="base_thickness", value="3,4", note=None
        )
    )
    assert (results[0].criterion.key, results[0].value) == ("base_thickness", "3.4")
    for criterion, value in (("base_thickness", "dày"), ("appearance", "ok"), ("weight", "1")):
        with pytest.raises(DomainError):
            asyncio.run(
                _recorder(world).handle(
                    rnd, case.id.value, criterion=criterion, value=value, note=None
                )
            )
    with pytest.raises(PermissionDeniedError):
        asyncio.run(
            _recorder(world).handle(
                world.context(scopes=frozenset({PRODUCT_CASE_WRITE})),
                case.id.value,
                criterion="appearance",
                value="pass",
                note=None,
            )
        )
    with pytest.raises(NotFoundError):
        asyncio.run(
            _recorder(world).handle(
                world.context(scopes=frozenset({PRODUCT_CASE_WRITE, RND}), workspace=uuid.uuid4()),
                case.id.value,
                criterion="appearance",
                value="pass",
                note=None,
            )
        )
    moved, _ = world.add_case(ProductDevState.PENDING_BOD_REVIEW)
    with pytest.raises(DomainError):
        asyncio.run(
            _recorder(world).handle(
                rnd, moved.id.value, criterion="appearance", value="pass", note=None
            )
        )


def test_a_requirement_is_kept_only_under_a_criterion_that_failed() -> None:
    from dw_supply_chain.domain.sample_evaluation import (
        Measurement,
        evidence,
        ground_evaluation,
        judge,
    )
    from dw_supply_chain.testing.step_preparation import NOW as AT

    results = judge(
        CRITERIA.default,
        [
            Measurement(uuid.uuid4(), 1, "base_thickness", "2.5", None, uuid.uuid4(), AT),
            Measurement(uuid.uuid4(), 1, "appearance", "pass", None, uuid.uuid4(), AT),
        ],
        1,
    )
    grounded = ground_evaluation(WRITING, evidence("Hồ sơ", results, ()), results)
    assert set(grounded.requirements) == {"base_thickness"}
    assert grounded.dropped == 2  # the note with 99%, the requirement for a pass


def test_a_measurement_on_another_workspace_s_case_is_not_found_even_through_a_leak() -> None:
    from dw_supply_chain.testing.supplier_messages import LeakyCases

    world = StepWorld(criteria=CRITERIA, cases=LeakyCases(leaky=True))
    case, _ = world.add_case(ProductDevState.SAMPLE_TESTING, workspace=uuid.uuid4())
    with pytest.raises(NotFoundError):
        asyncio.run(
            _recorder(world).handle(
                world.context(scopes=frozenset({PRODUCT_CASE_WRITE, RND})),
                case.id.value,
                criterion="appearance",
                value="pass",
                note=None,
            )
        )
    assert world.measurements.rows == []


def test_a_tenant_s_thresholds_never_judge_another_tenant() -> None:
    from dw_supply_chain.application.step_preparation import SamplePreparation

    strict = CRITERIA.model_dump(mode="json")
    strict["default"][0]["min"] = "5"

    class ByTenant:
        def __init__(self, owner: uuid.UUID) -> None:
            self.owner = owner

        async def get(self, context: Any, policy_id: str) -> Any:
            return strict if context.tenant_id == self.owner else None

        async def put(self, *args: Any, **kwargs: Any) -> None:
            raise NotImplementedError("not exercised")

    world = _world()
    case, _ = world.add_case(ProductDevState.SAMPLE_TESTING)
    world.measure(case, "base_thickness", "4")
    other = uuid.uuid4()
    sample = SamplePreparation(
        measurements=world.measurements,
        policy_override_repo=ByTenant(other),
        platform_default_criteria=CRITERIA,
    )
    results = asyncio.run(sample.results(world.context(), case))
    assert results[0].verdict.value == "pass"  # tenant `other`'s 5 mm is not ours
