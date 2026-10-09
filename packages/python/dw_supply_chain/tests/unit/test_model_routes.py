"""Unit: which model profile a Supply Chain task may run on (ticket
ai-automation/06). A route to a profile the deployment does not trust outright
loads only with a LIVE gate result that passed the task; and the extraction
lane reads the route."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.gates import ModelGateResult, TaskScore
from dw_agent_runtime.ports import ModelRequest, OutputT
from dw_kernel.errors import ConfigError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_supply_chain.application.document_extraction import ExtractDocuments, QueuedDocument
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
)
from dw_supply_chain.model_routes import (
    DRAFTING_TASKS,
    MODEL_TASKS,
    SupplyChainModelRoutes,
    load_supply_chain_model_routes,
)
from dw_supply_chain.testing.extraction import (
    PDF,
    InMemoryExtractionDocuments,
    InMemoryObjects,
    ListQueue,
    PlainTextReader,
    RecordingExtractions,
    StaticPlans,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SHIPPED = REPO_ROOT / "configs" / "policies" / "supply_chain_model_routes@1.5.0.yaml"
DATASET = "supply_chain_preparation@1.5.0"
TASK = "extract.sample_evaluation"
NOW = datetime(2026, 10, 9, tzinfo=UTC)


def _policy(routes: dict[str, str]) -> SupplyChainModelRoutes:
    return SupplyChainModelRoutes.model_validate(
        {
            "schema_version": "1.0",
            "policy_id": "supply_chain_model_routes",
            "policy_version": "1.0.0",
            "ungated_profiles": ["luna"],
            "gate": {"dataset": DATASET, "min_pass_rate": 1.0},
            "routes": routes,
        }
    )


def _gate(*, mode: str = "live", passed: int = 9, security_failed: int = 0) -> ModelGateResult:
    return ModelGateResult.model_validate(
        {
            "profile": "qwen",
            "dataset": DATASET,
            "mode": mode,
            "generated_at": NOW,
            "tasks": {TASK: TaskScore(total=9, passed=passed, security_failed=security_failed)},
        }
    )


def test_the_shipped_policy_routes_nothing_and_loads_without_any_gate(tmp_path: Path) -> None:
    policy = load_supply_chain_model_routes(SHIPPED, tmp_path)
    assert policy.routes == {} and policy.extraction_routes() == {}
    assert policy.gate.dataset == DATASET


def test_every_extractor_and_every_drafter_is_a_model_task() -> None:
    assert {
        "extract.sample_evaluation",
        "extract.supplier_confirmation_email",
        "extract.product_profile_bm04",
        "extract.supplier_quotation",
        "extract.proposal_list",
    } | DRAFTING_TASKS == MODEL_TASKS
    assert {
        "draft.supplier_message",
        "draft.sample_evaluation",
        "draft.bod_submission",
    } <= DRAFTING_TASKS


def test_a_drafting_task_runs_where_its_route_says_or_on_the_process_s_profile() -> None:
    assert _policy({}).profile_for("draft.supplier_message") is None
    assert _policy({"draft.supplier_message": "luna"}).profile_for("draft.supplier_message") == (
        "luna"
    )
    assert _policy({}).profile_for("extract.proposal_list") is None
    with pytest.raises(ValueError, match="not a model task"):
        _policy({}).profile_for("draft.nothing")


def test_a_route_to_an_ungated_profile_needs_no_gate() -> None:
    _policy({TASK: "luna"}).require_gates({})


@pytest.mark.parametrize(
    "results",
    [
        {},
        {"qwen": _gate(mode="mock")},
        {"qwen": _gate(passed=8)},
        {"qwen": _gate(security_failed=1)},
    ],
    ids=["no run", "mock run", "below threshold", "security case failed"],
)
def test_a_route_to_qwen_without_a_passing_live_gate_is_refused_by_name(
    results: dict[str, ModelGateResult],
) -> None:
    with pytest.raises(ConfigError, match=re.escape(f"{TASK} -> qwen")):
        _policy({TASK: "qwen"}).require_gates(results)


def test_a_live_pass_opens_that_task_and_only_that_task() -> None:
    policy = _policy({TASK: "qwen"})
    policy.require_gates({"qwen": _gate()})
    assert policy.extraction_routes() == {DocumentType.SAMPLE_EVALUATION: "qwen"}
    other = _policy({TASK: "qwen", "extract.supplier_quotation": "qwen"})
    with pytest.raises(ConfigError, match=r"extract.supplier_quotation -> qwen"):
        other.require_gates({"qwen": _gate()})


def test_the_loader_reads_the_committed_results(tmp_path: Path) -> None:
    policy_file = tmp_path / "routes.yaml"
    policy_file.write_text(
        SHIPPED.read_text(encoding="utf-8").replace("routes: {}", f"routes: {{{TASK}: qwen}}"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_supply_chain_model_routes(policy_file, tmp_path / "gates")
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "qwen.json").write_text(_gate().model_dump_json(), encoding="utf-8")
    assert load_supply_chain_model_routes(policy_file, tmp_path / "gates").routes == {TASK: "qwen"}


def test_a_route_for_a_task_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ValidationError, match="do not exist"):
        _policy({"extract.everything": "luna"})


class ProfileRecordingGateway:
    def __init__(self) -> None:
        self.profiles: list[str | None] = []

    async def generate_structured(
        self, request: ModelRequest, output_type: type[OutputT], *, run_context: RunContext
    ) -> OutputT:
        self.profiles.append(request.model_profile)
        return output_type()


async def test_the_extraction_lane_reads_on_the_routed_profile() -> None:
    tenant, workspace = uuid.uuid4(), uuid.uuid4()
    data = b"Ket luan: Dat"
    document = CaseDocument(
        id=CaseDocumentId(uuid.uuid4()),
        tenant_id=tenant,
        workspace_id=workspace,
        case_kind=CaseKind.PRODUCT,
        case_id=uuid.uuid4(),
        doc_type=DocumentType.SAMPLE_EVALUATION,
        object_key="k",
        filename="f.pdf",
        content_type=PDF,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        version=1,
        uploaded_by=uuid.uuid4(),
        uploaded_at=NOW,
    )
    gateway = ProfileRecordingGateway()
    extractions = RecordingExtractions()
    lane = ExtractDocuments(
        queue=ListQueue([QueuedDocument(tenant, workspace, document.id.value)]),
        documents=InMemoryExtractionDocuments([document]),
        storage=InMemoryObjects({"k": data}),
        text=PlainTextReader(),
        gateway=gateway,
        extractions=extractions,
        plans=StaticPlans({tenant: "professional"}),
        ids=Uuid4Generator(),
        clock=FixedClock(NOW),
        model_profile="luna",
        routes={DocumentType.SAMPLE_EVALUATION: "qwen"},
    )
    await lane.run_once()
    assert gateway.profiles == ["qwen"]
    assert extractions.rows[0][1].model_profile == "qwen"
