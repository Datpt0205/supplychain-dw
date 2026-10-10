"""Unit: step 12's pre-production test measured, drafted and filed like a
sample round (ticket ai-automation/17, item 2).

The real `RecordPreProductionMeasurement`, `GetPreProductionChecklist`,
`PreparePackagingPapers` and `TakePackagingStep` over `testing.packaging_papers`,
Elmich's own criteria (`noi`: base and wall thickness, induction, appearance),
the shipped prompt rendered through the real registry and the shipped
template rendered by the real DOCX renderer: R&D's values judged by code, the
record drafted once every criterion is measured with only the notes that
check out, code's suggestion beside an empty choice, and the record filed only
when it still prints what R&D's values judge to.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_kernel.errors import (
    ConflictError,
    DomainError,
    InfrastructureError,
    NotFoundError,
    PermissionDeniedError,
)
from dw_supply_chain.application.handlers import PO_CASE_READ
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.document_draft import DraftDecision
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.domain.packaging_design import (
    PackagingAction,
    PackagingHistoryEntry,
    PreProductionTest,
)
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.pre_production_test import attempt_of, suggested_test_action
from dw_supply_chain.domain.sample_evaluation import EvaluationWriting
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.packaging_papers import ORDERING, RND, PackagingWorld
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.supplier_messages import LeakyCases

pytestmark = pytest.mark.unit

A = PackagingAction
REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")
R_AND_D = frozenset({RND, PO_CASE_READ})
PASSING = {
    "base_thickness": "3.2",
    "wall_thickness": "0.6",
    "induction": "pass",
    "appearance": "pass",
}
WRITING = EvaluationWriting(
    notes=[
        CitedSentence(
            text="Độ dày đáy đo được 2.5 mm, thấp hơn chuẩn 3 mm.",
            cites=["criterion:base_thickness"],
        ),
        # A number no evidence it cites holds: dropped.
        CitedSentence(text="Mẫu đạt 99% yêu cầu khách hàng.", cites=["case"]),
    ]
)


def _world(answer: object = WRITING) -> PackagingWorld:
    return PackagingWorld(
        gateway=None if answer is None else ScriptedGateway(PROMPTS, answer=answer)  # type: ignore[arg-type]
    )


def _ready(world: PackagingWorld, values: dict[str, str] | None = None) -> POCase:
    """A case at step 12, its sample in, its product a pot (`noi`)."""
    case = world.add_case()
    world.add_product(case)
    world.receive_sample(case)
    for key, value in (values or {}).items():
        world.measure(case, key, value)
    return case


def _record(world: PackagingWorld, case: POCase, criterion: str, value: str, **kw: Any) -> Any:
    return asyncio.run(
        world.record().handle(
            world.context(kw.pop("scopes", R_AND_D), **kw),
            case.id,
            criterion=criterion,
            value=value,
            note=None,
        )
    )


def _reports(world: PackagingWorld) -> list[Any]:
    return world.drafted(DocumentType.PRE_PRODUCTION_TEST_REPORT)


def _take(world: PackagingWorld, case: POCase, action: A, **kw: Any) -> Any:
    return asyncio.run(
        world.take().handle(world.context(R_AND_D), po_case_id=case.id, action=action, **kw)
    )


def _history(*actions: A) -> list[PackagingHistoryEntry]:
    return [PackagingHistoryEntry(a, None, None, None, uuid.uuid4(), NOW) for a in actions]


# ------------------------------------------------------------------ domain --


def test_an_attempt_is_the_first_plus_one_per_failed_test() -> None:
    assert attempt_of([]) == 1
    assert attempt_of(_history(A.RECEIVE_PRE_PRODUCTION_SAMPLE)) == 1
    assert (
        attempt_of(
            _history(
                A.RECEIVE_PRE_PRODUCTION_SAMPLE,
                A.FAIL_PRE_PRODUCTION_TEST,
                A.FAIL_PRE_PRODUCTION_TEST,
            )
        )
        == 3
    )


def test_code_suggests_fail_on_a_failed_criterion_pass_on_all_and_nothing_while_one_is_open() -> (
    None
):
    world = _world(None)
    case = _ready(world, {**PASSING, "base_thickness": "2.5"})
    failing = asyncio.run(world.test().results(world.context(R_AND_D), case))
    assert suggested_test_action(failing.results) is A.FAIL_PRE_PRODUCTION_TEST

    world = _world(None)
    case = _ready(world, PASSING)
    assert (
        asyncio.run(world.test().results(world.context(R_AND_D), case)).suggestion
        is A.PASS_PRE_PRODUCTION_TEST
    )

    world = _world(None)
    case = _ready(world, {"base_thickness": "3.2"})
    results = asyncio.run(world.test().results(world.context(R_AND_D), case))
    assert results.suggestion is None and not results.complete


def test_the_criteria_follow_the_products_category_and_the_default_without_one() -> None:
    world = _world(None)
    case = _ready(world)
    keys = [c.key for c in asyncio.run(world.test().criteria(world.context(R_AND_D), case))]
    assert keys == ["base_thickness", "wall_thickness", "induction", "appearance"]

    other = world.add_case()
    world.add_product(other, workspace=uuid.uuid4())
    default = asyncio.run(world.test().criteria(world.context(R_AND_D), other))
    assert [c.key for c in default] == ["dimensions", "appearance", "function"]


# ------------------------------------------------------------------ record --


def test_rnd_records_a_canonical_value_under_the_current_attempt() -> None:
    world = _world(None)
    case = _ready(world)
    world.designs.events.extend(_history(A.FAIL_PRE_PRODUCTION_TEST))
    results = _record(world, case, "base_thickness", "3,2")
    (row,) = world.measurements.rows
    assert (row[2].attempt, row[2].value) == (2, "3.2")
    assert results.attempt == 2
    assert world.measurements.audits[0].action.endswith("pre_production_measurement.recorded")


def test_a_value_needs_the_duty_that_passes_the_test_before_anything_is_read() -> None:
    world = _world(None)
    case = _ready(world)
    with pytest.raises(PermissionDeniedError):
        _record(world, case, "base_thickness", "3.2", scopes=frozenset({ORDERING, PO_CASE_READ}))
    assert world.measurements.rows == []


def test_a_case_of_another_workspace_is_not_found() -> None:
    world = _world(None)
    case = _ready(world)
    with pytest.raises(NotFoundError):
        _record(world, case, "base_thickness", "3.2", workspace=uuid.uuid4())
    assert world.measurements.rows == []


@pytest.mark.parametrize(
    ("criterion", "value"),
    [("coating_thickness", "30"), ("base_thickness", "dày"), ("induction", "có")],
)
def test_a_criterion_off_the_list_or_a_value_it_does_not_take_is_refused(
    criterion: str, value: str
) -> None:
    world = _world(None)
    case = _ready(world)
    with pytest.raises(DomainError):
        _record(world, case, criterion, value)
    assert world.measurements.rows == []


def test_nothing_is_measured_before_the_sample_is_in_or_after_the_test_passed() -> None:
    world = _world(None)
    case = world.add_case()
    with pytest.raises(DomainError):
        _record(world, case, "base_thickness", "3.2")
    world.set_design(case, pre_production_test=PreProductionTest.PASSED)
    with pytest.raises(DomainError):
        _record(world, case, "base_thickness", "3.2")
    assert world.measurements.rows == []


def test_the_checklist_says_who_may_record_and_never_preselects() -> None:
    world = _world(None)
    case = _ready(world, {**PASSING, "appearance": "fail"})
    mine = asyncio.run(world.checklist().handle(world.context(R_AND_D), case.id))
    assert mine.can_record and mine.open
    assert mine.results.suggestion is A.FAIL_PRE_PRODUCTION_TEST
    buyer = asyncio.run(
        world.checklist().handle(world.context(frozenset({ORDERING, PO_CASE_READ})), case.id)
    )
    assert not buyer.can_record
    with pytest.raises(PermissionDeniedError):
        asyncio.run(world.checklist().handle(world.context(frozenset({RND})), case.id))


# -------------------------------------------------------------------- lane --


def test_no_record_is_drafted_while_a_criterion_is_unmeasured() -> None:
    world = _world()
    _ready(world, {"base_thickness": "2.5", "induction": "pass"})
    asyncio.run(world.lane().run())
    assert _reports(world) == []
    assert world.gateway.sent == []


def test_the_record_carries_codes_table_and_only_the_notes_that_check_out() -> None:
    world = _world()
    case = _ready(world, {**PASSING, "base_thickness": "2.5"})
    asyncio.run(world.lane().run())
    (draft,) = _reports(world)
    rows = draft.fields["criteria"]["value"]
    assert [r["result"] for r in rows] == ["Không đạt", "Đạt", "Đạt", "Đạt"]
    assert rows[0]["measured"] == "2.5 mm" and rows[0]["standard"] == "≥ 3 mm"
    assert draft.fields["notes"]["value"] == "Độ dày đáy đo được 2.5 mm, thấp hơn chuẩn 3 mm."
    assert draft.fields["notes"]["source"]["cites"] == ["criterion:base_thickness"]
    assert draft.fields["test_attempt"]["value"] == "1"
    assert draft.fields["po_reference"]["value"] == case.po_reference
    # The date, the tester and the conclusion stay a person's.
    assert {"tested_on", "conclusion"} <= set(draft.gaps)
    assert (draft.prompt_id, draft.prompt_version) == (
        "supply_chain.draft_sample_evaluation",
        "1.0.0",
    )
    (sent,) = world.gateway.sent
    assert '"key": "criterion:base_thickness"' in sent.user
    assert world.gateway.contexts[0].subject_ref == f"po_case:{case.id}"
    assert any(world.tester in n["recipients"] for n in world.notifier.sent)


def test_the_record_is_drafted_once_per_set_of_values_and_again_after_a_correction() -> None:
    world = _world()
    case = _ready(world, PASSING)
    asyncio.run(world.lane().run())
    asyncio.run(world.lane().run())
    assert len(_reports(world)) == 1
    world.measure(case, "appearance", "fail")
    asyncio.run(world.lane().run())
    assert len(_reports(world)) == 2


def test_without_a_model_answer_the_record_carries_the_table_alone() -> None:
    world = _world(InfrastructureError("the provider failed"))
    _ready(world, PASSING)
    asyncio.run(world.lane().run())
    (draft,) = _reports(world)
    assert "notes" not in draft.fields
    assert len(draft.fields["criteria"]["value"]) == 4


def test_values_of_another_workspace_never_complete_a_record() -> None:
    world = _world()
    case = _ready(world)
    for key, value in PASSING.items():
        world.measure(case, key, value, workspace=uuid.uuid4())
    asyncio.run(world.lane().run())
    assert _reports(world) == []
    assert world.gateway.sent == []


# -------------------------------------------------------------------- file --


def _drafted(world: PackagingWorld, values: dict[str, str] = PASSING) -> tuple[POCase, Any]:
    case = _ready(world, values)
    asyncio.run(world.lane().run())
    (draft,) = _reports(world)
    return case, draft


def test_rnd_takes_the_test_with_the_drafted_record_which_is_filed_and_confirmed() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    design = _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id)
    assert design.pre_production_test is PreProductionTest.PASSED
    (filed,) = [
        d for d in world.documents.rows if d.doc_type is DocumentType.PRE_PRODUCTION_TEST_REPORT
    ]
    assert world.designs.events[-1].document_id == filed.id.value
    assert world.drafts.decisions[draft.id][0] is DraftDecision.CONFIRMED
    assert world.storage.objects[filed.object_key][0]


def test_a_record_whose_values_changed_since_is_refused_and_nothing_is_filed() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    world.measure(case, "appearance", "fail")
    with pytest.raises(ConflictError):
        _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id)
    assert draft.id not in world.drafts.decisions
    assert world.storage.objects == {}


def test_a_record_of_another_case_or_workspace_is_the_one_refusal() -> None:
    world = _world(None)
    _, draft = _drafted(world)
    other = _ready(world, PASSING)
    with pytest.raises(ConflictError) as refused:
        _take(world, other, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id)
    assert refused.value.details["missing_document_type"] == "pre_production_test_report"
    with pytest.raises(ConflictError):
        _take(world, other, A.PASS_PRE_PRODUCTION_TEST, draft_id=uuid.uuid4())
    assert world.storage.objects == {}


def test_a_failed_test_needs_its_reason_before_anything_is_filed() -> None:
    world = _world(None)
    case, draft = _drafted(world, {**PASSING, "appearance": "fail"})
    with pytest.raises(DomainError):
        _take(world, case, A.FAIL_PRE_PRODUCTION_TEST, draft_id=draft.id)
    assert world.storage.objects == {}
    design = _take(world, case, A.FAIL_PRE_PRODUCTION_TEST, draft_id=draft.id, reason="Móp ở thành")
    assert design.pre_production_test is PreProductionTest.FAILED


def test_a_draft_goes_only_with_a_test_step_and_never_beside_an_upload() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    with pytest.raises(DomainError):
        _take(world, case, A.APPROVE_DESIGN, draft_id=draft.id)
    with pytest.raises(DomainError):
        _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id, document_id=uuid.uuid4())
    assert world.storage.objects == {}


def test_a_decided_record_or_a_closed_test_files_nothing() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id)
    filed = len(world.storage.objects)
    with pytest.raises(ConflictError):
        _take(world, case, A.FAIL_PRE_PRODUCTION_TEST, draft_id=draft.id, reason="x")
    assert len(world.storage.objects) == filed


def test_after_a_failed_test_the_next_attempt_starts_empty() -> None:
    world = _world(None)
    case, draft = _drafted(world, {**PASSING, "appearance": "fail"})
    _take(world, case, A.FAIL_PRE_PRODUCTION_TEST, draft_id=draft.id, reason="Móp")
    results = asyncio.run(world.test().results(world.context(R_AND_D), case))
    assert results.attempt == 2 and not results.complete
    assert all(r.value is None for r in results.results)


def test_measuring_after_the_sample_came_in_long_ago_still_counts() -> None:
    world = _world(None)
    case = world.add_case()
    world.add_product(case)
    world.receive_sample(case).pre_production_sample_received_at = NOW - timedelta(days=40)
    _record(world, case, "base_thickness", "3.2")
    assert len(world.measurements.rows) == 1


# ------------------------------------------------- second layers, survivors --


def test_a_case_a_leaky_store_lets_through_is_still_not_found() -> None:
    world = _world(None)
    case = _ready(world)
    world.store.leaky = True
    with pytest.raises(NotFoundError):
        _record(world, case, "base_thickness", "3.2", workspace=uuid.uuid4())
    assert world.measurements.rows == []


def test_a_product_a_leaky_store_lets_through_does_not_pick_the_criteria() -> None:
    world = PackagingWorld(product_cases=LeakyCases(leaky=True))
    case = world.add_case()
    world.add_product(case, workspace=uuid.uuid4())
    keys = [c.key for c in asyncio.run(world.test().criteria(world.context(R_AND_D), case))]
    assert keys == ["dimensions", "appearance", "function"]


def test_nothing_is_measured_for_a_known_criterion_before_the_sample_is_in() -> None:
    world = _world(None)
    case = world.add_case()
    world.add_product(case)
    with pytest.raises(DomainError):
        _record(world, case, "base_thickness", "3.2")
    assert world.measurements.rows == []


def test_a_draft_of_another_type_on_the_case_is_the_one_refusal() -> None:
    world = _world(None)
    case, report = _drafted(world)
    other = replace(report, id=uuid.uuid4(), doc_type=DocumentType.DESIGN_REVISION_REQUEST)
    world.drafts.rows.append(replace(other, lineage_id=other.id))
    with pytest.raises(ConflictError):
        _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=other.id)
    assert world.storage.objects == {}


def test_a_rejected_record_is_not_filed_while_the_test_is_open() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    world.drafts.decisions[draft.id] = (DraftDecision.REJECTED, "sai", uuid.uuid4())
    with pytest.raises(ConflictError):
        _take(world, case, A.PASS_PRE_PRODUCTION_TEST, draft_id=draft.id)
    assert world.storage.objects == {}


def test_a_closed_test_files_nothing_even_with_an_open_record() -> None:
    world = _world(None)
    case, draft = _drafted(world)
    world.designs.rows[case.id.value][2].pre_production_test = PreProductionTest.PASSED
    with pytest.raises(ConflictError):
        _take(world, case, A.FAIL_PRE_PRODUCTION_TEST, draft_id=draft.id, reason="x")
    assert world.storage.objects == {}
