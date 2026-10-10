"""Unit: questions about product-development cases (stage-1 ticket 08) — what
the model may claim, how code grounds and resolves it, and what the command
bar's handler then reads, under whose authorization.

"An answer is never broader than the question": every case below where a
field cannot be applied, grounded or resolved ends in a refusal, never in a
list without that narrowing.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelOutputInvalidError, ModelRequest
from dw_kernel.errors import PermissionDeniedError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_kernel.ports import Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_supply_chain.application.case_query import AnswerCaseQuery
from dw_supply_chain.application.handlers import (
    PO_CASE_READ,
    PRODUCT_CASE_READ,
    ListPOCases,
    ListProductCategories,
)
from dw_supply_chain.application.product_cases import ListProductCases
from dw_supply_chain.domain.case_query import (
    CaseQueryIntent,
    CaseQueryKind,
    CaseQueryOutcome,
    GroundedField,
    ground,
    ignored_fields,
    plan_case_query,
)
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
)
from dw_supply_chain.policy_files import SLA_POLICY_FILE
from dw_supply_chain.sla_policy import ProductCategory, load_supply_chain_sla_policy
from dw_supply_chain.testing.po_cases import InMemoryPOCases
from dw_supply_chain.testing.product_cases import (
    InMemoryDirectory,
    InMemoryProductCases,
    Member,
)

pytestmark = pytest.mark.unit

_POLICIES = Path(__file__).resolve().parents[5] / "configs" / "policies"
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
ASKER, LAN, LAN_TOO = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CATEGORIES = [ProductCategory(key="noi", label="Nồi"), ProductCategory(key="chao", label="Chảo")]
MEMBERS = [(LAN, "Nguyễn Thị Lan"), (ASKER, "Trần Văn Hùng")]


def _plan(question: str, answer: Mapping[str, object], **known: Any) -> Any:
    grounded = ground(CaseQueryIntent.model_validate(dict(answer)), question)
    return grounded, plan_case_query(
        grounded,
        known.get("suppliers", []),
        categories=known.get("categories", CATEGORIES),
        members=known.get("members", MEMBERS),
        caller=known.get("caller", ASKER),
    )


# ---- the domain -----------------------------------------------------------------


def test_a_list_narrowed_by_state_category_and_a_named_pic() -> None:
    _, plan = _plan(
        "hồ sơ Chảo của Nguyễn Thị Lan đang test mẫu",
        {
            "kind": "list_product_cases",
            "category_mention": "Chảo",
            "pic_mention": "Nguyễn Thị Lan",
            "product_state": "sample_testing",
            "product_state_quote": "đang test mẫu",
        },
    )
    assert plan.outcome is CaseQueryOutcome.PRODUCT_LIST
    assert (plan.category, plan.pic_user_id, plan.product_state) == (
        "chao",
        LAN,
        ProductDevState.SAMPLE_TESTING,
    )


def test_mine_is_the_asker_and_never_a_name() -> None:
    _, plan = _plan(
        "hồ sơ của tôi", {"kind": "list_product_cases", "mine_quote": "của tôi"}, caller=ASKER
    )
    assert plan.outcome is CaseQueryOutcome.PRODUCT_LIST and plan.pic_user_id == ASKER


def test_mine_with_nobody_asking_narrows_to_nobody() -> None:
    _, plan = _plan(
        "hồ sơ của tôi", {"kind": "list_product_cases", "mine_quote": "của tôi"}, caller=None
    )
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


def test_a_named_pic_and_mine_together_are_refused() -> None:
    _, plan = _plan(
        "hồ sơ của tôi và Nguyễn Thị Lan",
        {
            "kind": "list_product_cases",
            "mine_quote": "của tôi",
            "pic_mention": "Nguyễn Thị Lan",
        },
    )
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert set(plan.unused) == {GroundedField.PIC, GroundedField.MINE}


@pytest.mark.parametrize(
    ("question", "answer", "outcome"),
    [
        # A Category the tenant does not list: refused, never "every Category".
        (
            "hồ sơ Ấm đun",
            {"kind": "list_product_cases", "category_mention": "Ấm đun"},
            CaseQueryOutcome.CATEGORY_NOT_FOUND,
        ),
        # A person not in the workspace.
        (
            "hồ sơ của Phạm Minh",
            {"kind": "list_product_cases", "pic_mention": "Phạm Minh"},
            CaseQueryOutcome.PIC_NOT_FOUND,
        ),
    ],
)
def test_what_does_not_resolve_stops_the_list(
    question: str, answer: dict[str, object], outcome: CaseQueryOutcome
) -> None:
    _, plan = _plan(question, answer)
    assert plan.outcome is outcome


def test_two_people_sharing_a_name_are_ambiguous_not_picked() -> None:
    _, plan = _plan(
        "hồ sơ của Lan",
        {"kind": "list_product_cases", "pic_mention": "Lan"},
        members=[(LAN, "Lan"), (LAN_TOO, "Lan")],
    )
    assert plan.outcome is CaseQueryOutcome.PIC_AMBIGUOUS and plan.pic_user_id is None


def test_open_without_a_code_asks_for_one() -> None:
    """Missing evidence: "mở hồ sơ" with no code opens nothing."""
    _, plan = _plan("mở hồ sơ phát triển giúp tôi", {"kind": "open_product_case"})
    assert plan.outcome is CaseQueryOutcome.PROPOSAL_CODE_MISSING


def test_a_code_the_question_does_not_contain_is_never_opened() -> None:
    grounded, plan = _plan(
        "hồ sơ phát triển tới đâu rồi",
        {"kind": "open_product_case", "proposal_code_mention": "SP-028"},
    )
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert ignored_fields(grounded) == (GroundedField.PROPOSAL_CODE,)


def test_a_code_cut_from_a_longer_one_is_not_opened() -> None:
    _, plan = _plan(
        "hồ sơ SP-028-A tới đâu",
        {"kind": "open_product_case", "proposal_code_mention": "SP-028"},
    )
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD


@pytest.mark.parametrize(
    ("question", "answer", "unused"),
    [
        # A PO filter on a product list: refused, never dropped.
        (
            "hồ sơ phát triển của NCC Sunhouse",
            {"kind": "list_product_cases", "supplier_mention": "Sunhouse"},
            {GroundedField.SUPPLIER},
        ),
        # A product filter on a PO list.
        (
            "PO nhóm Chảo",
            {"kind": "list_cases", "category_mention": "Chảo"},
            {GroundedField.CATEGORY},
        ),
        # Opening one case applies no list filter.
        (
            "hồ sơ SP-1 nhóm Nồi",
            {
                "kind": "open_product_case",
                "proposal_code_mention": "SP-1",
                "category_mention": "Nồi",
            },
            {GroundedField.CATEGORY},
        ),
        # "đang chạy" is a PO list's filter only.
        (
            "hồ sơ phát triển đang chạy",
            {"kind": "list_product_cases", "active_only_quote": "đang chạy"},
            {GroundedField.ACTIVE_ONLY},
        ),
    ],
)
def test_a_field_of_the_other_kind_is_refused_not_dropped(
    question: str, answer: dict[str, object], unused: set[GroundedField]
) -> None:
    _, plan = _plan(question, answer)
    assert plan.outcome is CaseQueryOutcome.NOT_UNDERSTOOD
    assert set(plan.unused) == unused


@pytest.mark.parametrize(
    "answer",
    [
        # Cross tenant: a tenant field smuggled into the reading.
        {"kind": "list_product_cases", "tenant_id": "00000000-0000-0000-0000-000000000001"},
        # A PIC as an id the model chose rather than words of the question.
        {"kind": "list_product_cases", "pic_user_id": str(uuid.uuid4())},
        # A state outside the product's closed set.
        {"kind": "list_product_cases", "product_state": "shipped", "product_state_quote": "x"},
        # A PO state in the product's slot.
        {
            "kind": "list_product_cases",
            "product_state": "waiting_deposit",
            "product_state_quote": "x",
        },
    ],
)
def test_the_schema_refuses_a_field_it_does_not_have(answer: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CaseQueryIntent.model_validate(answer)


# ---- the handler ------------------------------------------------------------------


@dataclass
class _Model:
    answer: Mapping[str, object]
    calls: list[RunContext] = field(default_factory=list)

    async def generate_structured[T: BaseModel](
        self, request: ModelRequest, output_type: type[T], *, run_context: RunContext
    ) -> T:
        self.calls.append(run_context)
        try:
            return output_type.model_validate(dict(self.answer))
        except ValidationError as exc:
            raise ModelOutputInvalidError("model output failed schema validation") from exc


class _NoOverrides:
    async def get(self, context: AccessContext, policy_id: str) -> None:
        return None

    async def put(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("not exercised by a question")


def _product(
    code: str,
    *,
    state: ProductDevState = ProductDevState.SAMPLE_TESTING,
    category: str = "noi",
    pic: uuid.UUID = LAN,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    age: int = 0,
) -> ProductDevelopmentCase:
    return ProductDevelopmentCase(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(tenant),
        workspace_id=WorkspaceId(workspace),
        proposal_code=code,
        product_name=f"Sản phẩm {code}",
        category=category,
        pic_user_id=pic,
        created_by=pic,
        state=state,
        created_at=NOW - timedelta(minutes=age),
    )


@dataclass
class _Bench:
    products: InMemoryProductCases = field(default_factory=InMemoryProductCases)
    directory: InMemoryDirectory = field(default_factory=InMemoryDirectory)

    def handler(self, model: _Model) -> AnswerCaseQuery:
        authz = ScopeAuthorizationService()
        po_cases = InMemoryPOCases()
        return AnswerCaseQuery(
            po_case_repo=po_cases,
            list_cases=ListPOCases(repo=po_cases, authz=authz),
            product_cases=self.products,
            list_product_cases=ListProductCases(repo=self.products, authz=authz),
            categories=ListProductCategories(
                policy_override_repo=_NoOverrides(),
                platform_default_sla_policy=load_supply_chain_sla_policy(
                    _POLICIES / SLA_POLICY_FILE
                ),
                authz=authz,
            ),
            directory=self.directory,
            gateway=model,
            authz=authz,
            ids=Uuid4Generator(),
        )


def _context(
    *,
    tenant: uuid.UUID = TENANT,
    workspace: uuid.UUID = WORKSPACE,
    scopes: frozenset[str] = frozenset({PO_CASE_READ, PRODUCT_CASE_READ}),
) -> AccessContext:
    return AccessContext(
        tenant_id=tenant,
        workspace_id=workspace,
        principal_id=ASKER,
        roles=frozenset(),
        scopes=scopes,
        plan_id="professional",
    )


async def test_a_list_returns_only_what_its_resolved_filter_matches() -> None:
    bench = _Bench()
    wanted = _product("SP-1", category="chao", state=ProductDevState.SAMPLE_TESTING)
    bench.products.seed(
        [
            wanted,
            _product("SP-2", category="noi", state=ProductDevState.SAMPLE_TESTING),
            _product("SP-3", category="chao", state=ProductDevState.PROPOSED),
        ]
    )
    answer = await bench.handler(
        _Model(
            {
                "kind": "list_product_cases",
                "category_mention": "Chảo",
                "product_state": "sample_testing",
                "product_state_quote": "đang test mẫu",
            }
        )
    ).handle(_context(), "hồ sơ Chảo đang test mẫu")
    assert answer.plan.outcome is CaseQueryOutcome.PRODUCT_LIST
    assert answer.product_cases == (wanted,)
    assert answer.intent is CaseQueryKind.LIST_PRODUCT_CASES


async def test_a_named_pic_resolves_against_the_askers_workspace_only() -> None:
    bench = _Bench()
    elsewhere = uuid.uuid4()
    bench.directory.members.extend(
        [
            Member(LAN, "Nguyễn Thị Lan", TENANT, WORKSPACE),
            # The same name in another workspace is not a candidate.
            Member(LAN_TOO, "Nguyễn Thị Lan", TENANT, elsewhere),
        ]
    )
    bench.products.seed([_product("SP-1", pic=LAN), _product("SP-2", pic=ASKER)])
    answer = await bench.handler(
        _Model({"kind": "list_product_cases", "pic_mention": "Nguyễn Thị Lan"})
    ).handle(_context(), "hồ sơ của Nguyễn Thị Lan")
    assert answer.plan.pic_user_id == LAN
    assert [c.proposal_code for c in answer.product_cases] == ["SP-1"]


async def test_open_by_code_returns_the_stored_case() -> None:
    bench = _Bench()
    case = _product("SP-028")
    bench.products.seed([case])
    answer = await bench.handler(
        _Model({"kind": "open_product_case", "proposal_code_mention": "sp-028"})
    ).handle(_context(), "hồ sơ sp-028 tới đâu rồi?")
    assert answer.plan.outcome is CaseQueryOutcome.PRODUCT_OPEN
    assert answer.opened_product == case
    # The stored code, not the question's spelling.
    assert answer.plan.proposal_code == "SP-028"


@pytest.mark.parametrize("where", ["tenant", "workspace"])
async def test_a_code_only_elsewhere_reads_as_not_found(where: str) -> None:
    """Negative: a code of tenant B, or of workspace W1 asked from W2, is not
    found, and nothing of that case is in the answer."""
    bench = _Bench()
    elsewhere = _product(
        "SP-777",
        tenant=uuid.uuid4() if where == "tenant" else TENANT,
        workspace=uuid.uuid4(),
    )
    bench.products.seed([elsewhere])
    answer = await bench.handler(
        _Model({"kind": "open_product_case", "proposal_code_mention": "SP-777"})
    ).handle(_context(), "hồ sơ SP-777 tới đâu rồi?")
    assert answer.plan.outcome is CaseQueryOutcome.PRODUCT_NOT_FOUND
    assert answer.opened_product is None and answer.product_cases == ()


async def test_a_list_never_holds_another_workspaces_case() -> None:
    bench = _Bench()
    bench.products.seed([_product("SP-W1"), _product("SP-W2", workspace=uuid.uuid4())])
    answer = await bench.handler(_Model({"kind": "list_product_cases"})).handle(
        _context(), "các hồ sơ phát triển"
    )
    assert [c.proposal_code for c in answer.product_cases] == ["SP-W1"]


async def test_a_product_question_needs_the_product_read_scope() -> None:
    bench = _Bench()
    bench.products.seed([_product("SP-1")])
    with pytest.raises(PermissionDeniedError):
        await bench.handler(_Model({"kind": "list_product_cases"})).handle(
            _context(scopes=frozenset({PO_CASE_READ})), "các hồ sơ phát triển"
        )


async def test_opening_a_product_case_needs_the_product_read_scope() -> None:
    """The open path reads the repository directly (no list handler around
    it), so the handler's own check is the only one in the way."""
    bench = _Bench()
    bench.products.seed([_product("SP-1")])
    with pytest.raises(PermissionDeniedError):
        await bench.handler(
            _Model({"kind": "open_product_case", "proposal_code_mention": "SP-1"})
        ).handle(_context(scopes=frozenset({PO_CASE_READ})), "hồ sơ SP-1 tới đâu?")


async def test_without_po_read_no_token_is_spent() -> None:
    model = _Model({"kind": "list_product_cases"})
    with pytest.raises(PermissionDeniedError):
        await (
            _Bench()
            .handler(model)
            .handle(_context(scopes=frozenset({PRODUCT_CASE_READ})), "các hồ sơ phát triển")
        )
    assert model.calls == []
