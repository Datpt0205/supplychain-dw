"""Unit: the tờ trình BGĐ, drafted before BGĐ's review is raised (step 6;
ticket ai-automation/10).

The real `PrepareBodSubmission` over the in-memory world of
`testing.bod_submissions`, the shipped prompt rendered through the real
registry; and the real `EnsureProductApproval` asking for it before it starts
the review.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_agent_runtime.ports import ModelOutputInvalidError
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.application.document_drafts import GetDocumentDraft
from dw_supply_chain.application.ports import ReviewRaise, ReviewRequester
from dw_supply_chain.application.product_reviews import EnsureProductApproval
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.grounded_writing import CitedSentence
from dw_supply_chain.product_approvals import load_supply_chain_product_approvals
from dw_supply_chain.testing.bod_submissions import SubmissionWorld
from dw_supply_chain.testing.extraction import ScriptedGateway
from dw_supply_chain.testing.supplier_messages import LeakyCases

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
PROMPTS = load_shipped_prompts(REPO_ROOT / "configs")
WRITING_FIELDS: dict[str, Any] = {
    "summary": [
        CitedSentence(
            text="Mẫu Nồi inox 3 đáy 24cm đạt mọi tiêu chí, độ dày đáy 3.2 mm.",
            cites=["draft", "case"],
        ),
        CitedSentence(text="Đơn giá 245.000 đồng là hợp lý.", cites=["case"]),
        CitedSentence(text="Theo hồ sơ DX-2026-999 cùng NCC.", cites=["doc:other-case"]),
    ],
    "risks": [CitedSentence(text="Thời gian giao 45 ngày.", cites=["doc"])],
    "recommendation": [CitedSentence(text="Đề nghị BGĐ duyệt sản phẩm.", cites=["case"])],
}


def _writing(keys: Mapping[str, str]) -> Any:
    from dw_supply_chain.application.bod_submissions import BodSubmissionWriting

    def resolve(sentences: list[CitedSentence]) -> list[CitedSentence]:
        return [
            CitedSentence(text=s.text, cites=[keys.get(c, c) for c in s.cites]) for s in sentences
        ]

    return BodSubmissionWriting(**{k: resolve(v) for k, v in WRITING_FIELDS.items()})


def _world(answer: Any = None) -> SubmissionWorld:
    return SubmissionWorld(gateway=ScriptedGateway(PROMPTS, answer=answer))


def _prepare(world: SubmissionWorld, **kw: Any) -> tuple[Any, Any]:
    case = world.case(**kw)
    keys = {
        "draft": f"draft:{world.world.drafts.rows[0].id}" if world.world.drafts.rows else "draft",
        "doc": f"doc:{world.world.documents.rows[0].id}" if world.world.documents.rows else "doc",
    }
    if world.gateway.answer is None:
        world.gateway.answer = _writing(keys)
    ref = asyncio.run(world.submitter().prepare(world.context(case), case))
    return case, ref


def _fields(world: SubmissionWorld) -> Mapping[str, Any]:
    [draft] = [d for d in world.world.drafts.rows if d.doc_type is DocumentType.BOD_SUBMISSION]
    return draft.fields


def test_code_fills_the_facts_and_only_grounded_sentences_are_kept() -> None:
    world = _world()
    _, ref = _prepare(world)
    assert ref is not None
    fields = _fields(world)
    assert fields["proposal_code"]["value"] == "DX-2026-041"
    assert fields["evaluation_result"]["value"] == "Đạt (vòng 1)"
    assert fields["unit_price"]["value"] == "245000"
    assert fields["unit_price"]["source"]["quote"] == "đơn giá 245.000 VND"
    summary = fields["summary"]["value"]
    assert "3.2 mm" in summary and "45 ngày" in summary
    assert "245.000" not in summary and "DX-2026-999" not in summary
    assert fields["summary"]["source"]["ai_written"] is True
    assert fields["recommendation"]["value"] == "Đề nghị BGĐ duyệt sản phẩm."


def test_the_model_never_sees_a_price() -> None:
    world = _world()
    _prepare(world)
    [sent] = world.gateway.sent
    for price in ("245.000", "245000", "245.000.000"):
        assert price not in sent.user and price not in sent.system


def test_a_round_s_submission_is_drafted_once() -> None:
    world = _world()
    case, ref = _prepare(world)
    again = asyncio.run(world.submitter().prepare(world.context(case), case))
    assert again is not None and ref is not None and again.draft_id == ref.draft_id
    assert len(world.gateway.sent) == 1


def test_a_case_of_another_workspace_drafts_nothing_and_calls_nothing() -> None:
    world = _world()
    world.world.cases = LeakyCases(leaky=True)
    case = world.case(workspace=uuid.uuid4())
    other = world.world.context()
    assert asyncio.run(world.submitter().prepare(other, case)) is None
    assert world.gateway.sent == []


def test_no_model_writing_is_no_submission() -> None:
    world = _world(ModelOutputInvalidError("bad"))
    _, ref = _prepare(world)
    assert ref is None
    assert not [d for d in world.world.drafts.rows if d.doc_type is DocumentType.BOD_SUBMISSION]


def test_a_reader_without_commercial_read_sees_the_price_redacted() -> None:
    world = _world()
    case, ref = _prepare(world)
    assert ref is not None
    reader = GetDocumentDraft(
        drafts=world.world.drafts,
        templates=world.world.templates,
        authz=ScopeAuthorizationService(),
    )
    base = frozenset({"supply_chain.document.read"})
    plain = asyncio.run(
        reader.handle(world.world.context(tenant=case.tenant_id.value, scopes=base), ref.draft_id)
    )
    assert plain.draft.fields["unit_price"]["value"] is None
    assert plain.draft.fields["unit_price"]["redacted"] is True
    full = asyncio.run(
        reader.handle(
            world.world.context(
                tenant=case.tenant_id.value, scopes=base | {"supply_chain.commercial.read"}
            ),
            ref.draft_id,
        )
    )
    assert full.draft.fields["unit_price"]["value"] == "245000"


# ----------------------------------------------------------- the review --


@dataclass
class _Runner:
    order: list[str]
    started: list[dict[str, Any]] = field(default_factory=list)

    async def start(self, *, run_context: RunContext, input_payload: dict[str, Any]) -> uuid.UUID:
        self.order.append("start")
        self.started.append(input_payload)
        return run_context.run_id


@dataclass
class _Approvals:
    async def raised_by_payload(self, context: Any, **kwargs: Any) -> None:
        return None


@dataclass
class _Submissions:
    order: list[str]
    fail: bool = False

    async def prepare(self, context: Any, case: Any) -> Any:
        self.order.append("prepare")
        if self.fail:
            raise RuntimeError("drafter down")

        @dataclass
        class Ref:
            def as_json(self) -> dict[str, Any]:
                return {"draft_id": "d", "content_sha256": "a" * 64, "gaps": []}

        return Ref()


class _NoOverrides:
    async def get(self, context: Any, policy_id: str) -> None:
        return None

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised")


class _Nobody:
    async def holding(self, *args: Any) -> list[uuid.UUID]:
        return []


class _Quiet:
    async def deliver(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.mark.parametrize("fail", [False, True])
def test_the_review_is_raised_after_the_submission_and_without_one_when_it_fails(
    fail: bool,
) -> None:
    from dw_supply_chain.policy_files import PRODUCT_APPROVALS_POLICY_FILE

    world = _world()
    case = world.case()
    order: list[str] = []
    runner = _Runner(order)
    ensure = EnsureProductApproval(
        runner=runner,
        approvals=_Approvals(),
        holders=_Nobody(),
        notifier=_Quiet(),
        policy_override_repo=_NoOverrides(),
        platform_default_approvals=load_supply_chain_product_approvals(
            REPO_ROOT / "configs" / "policies" / PRODUCT_APPROVALS_POLICY_FILE
        ),
        ids=Uuid4Generator(),
        submissions=_Submissions(order, fail=fail),
    )
    context = world.context(case)
    outcome = asyncio.run(ensure.ensure(context, case, ReviewRequester.from_context(context)))
    assert outcome is ReviewRaise.NOT_RAISED  # the fake raises nothing to find
    assert order == ["prepare", "start"]
    [payload] = runner.started
    assert (payload["bod_submission"] is None) is fail


def test_a_tenant_that_did_not_ask_for_a_submission_gets_none_and_no_call() -> None:
    world = SubmissionWorld(gateway=ScriptedGateway(PROMPTS, answer=None), enabled=False)
    case = world.case()
    assert asyncio.run(world.submitter().prepare(world.context(case), case)) is None
    assert world.gateway.sent == []


def test_a_record_without_a_conclusion_leaves_the_evaluation_result_empty() -> None:
    world = _world()
    case = world.case()
    [record] = world.world.drafts.rows
    world.world.drafts.rows[0] = type(record)(
        **{
            **{f: getattr(record, f) for f in record.__dataclass_fields__},
            "fields": {**record.fields, "conclusion": {"value": None, "source": None}},
        }
    )
    world.gateway.answer = _writing({})
    ref = asyncio.run(world.submitter().prepare(world.context(case), case))
    assert ref is not None and "evaluation_result" in ref.gaps
    assert "evaluation_result" not in _fields(world)
