"""Integration: the step-9 sign-off by BGĐ then Kế toán, through the real
runner, the Postgres checkpointer, `ApproveAndResumeService`, the approval
inbox and the reconcile lane (stage-1 ticket 04, ADR 0016, ADR 0020).

What only the real thing can show:

- `submit_for_signoff` raises exactly one approval, stamped with the FIRST
  step's scope, and tells who may decide it; BGĐ's decision resumes the SAME
  run, which raises Kế toán's step stamped with its own scope, and the lane
  tells Kế toán; Kế toán's decision applies `signoff_approve`, the last signer
  the actor;
- a decider without the stamped scope (Kế toán on BGĐ's step, a manager,
  `platform_admin`) does not find the step, the requester cannot decide it,
  and a decider of another workspace or tenant finds nothing;
- a step not approved returns the case to item coding with its codes kept;
- a tenant's new order reaches cases submitted after it only;
- a case cancelled while a step waits applies nothing and raises no further
  step;
- Kế toán decides on Zalo after a view (ADR 0014), through the same
  case-version port as BGĐ's review.

The stack is `test_product_bod_review.ReviewStack`, wired as the API wires
both graphs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from supply_chain_harness import DatabaseUrls
from test_product_bod_review import (
    _APPROVALS,
    ALPHA,
    ALPHA_WS,
    BETA,
    BOD_SCOPE,
    OPERATOR_SCOPES,
    PDF,
    World,
    _another_workspace,
    _audit,
    _code,
    _context,
    _count,
    _linked,
    _member,
    _NoRunsLeft,
    _send,
    _zalo,
)
from test_product_bod_review import ReviewStack as Stack

from dw_agent_runtime.adapters.run_store import RunStatus
from dw_kernel.errors import ConflictError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import SystemClock
from dw_platform.adapters.persistence.notifications import SqlNotificationRepository
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.application.notifications import NotificationService
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus
from dw_platform.domain.audit import AuditEvent
from dw_platform.testing.seed_env import seed_test_env
from dw_supply_chain.adapters.persistence.case_document_repository import (
    SqlCaseDocumentRepository,
)
from dw_supply_chain.application.ports import NewCaseDocument, ReviewRaise
from dw_supply_chain.domain.case_document import (
    CaseDocument,
    CaseDocumentId,
    CaseKind,
    DocumentType,
    ObjectKey,
)
from dw_supply_chain.domain.product_development_case import (
    ProductAction,
    ProductDevelopmentCase,
    ProductDevelopmentCaseId,
    ProductDevState,
    SkuDraft,
)
from dw_supply_chain.product_approvals import (
    PRODUCT_APPROVALS_POLICY_ID,
    load_supply_chain_product_approvals,
)
from dw_supply_chain.workflows.advance_product_case_graph import (
    APPROVAL_TYPE_PREFIX,
    BOD_REVIEW_CASE_KEY,
    SUPERSEDED,
)
from dw_supply_chain.workflows.product_signoff_graph import SIGNOFF_APPROVAL_TYPE

pytestmark = pytest.mark.integration

ACCOUNTING_SCOPE = "supply_chain.approve.accounting"
_DECIDE = ScopeAuthorizationService()


@pytest.fixture
async def world(db_urls: DatabaseUrls) -> AsyncIterator[World]:
    """As `test_product_bod_review.world`: the seeded tenants, one stack."""
    await seed_test_env(db_urls.migrator)
    migrator = create_async_engine(db_urls.migrator, poolclass=NullPool)
    stack = Stack(db_urls.app)
    yield World(db_urls, migrator, stack)
    await stack.dispose()
    await migrator.dispose()


async def _document(
    world: World, actor: AccessContext, case_id: uuid.UUID, doc_type: DocumentType
) -> CaseDocument:
    document_id = CaseDocumentId(uuid.uuid4())
    return await SqlCaseDocumentRepository(world.stack.sessions).add(
        actor,
        NewCaseDocument(
            id=document_id,
            case_kind=CaseKind.PRODUCT,
            case_id=case_id,
            doc_type=doc_type,
            object_key=ObjectKey.build(
                tenant_id=ALPHA,
                workspace_id=ALPHA_WS,
                case_kind=CaseKind.PRODUCT,
                case_id=case_id,
                document_id=document_id.value,
            ).value,
            filename="giay-to.pdf",
            content_type="application/pdf",
            size_bytes=len(PDF),
            sha256="0" * 64,
        ),
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            actor_id=UserId(actor.principal_id),
            action="supply_chain.document.upload",
            resource_type="case_document",
            resource_id=str(document_id),
            occurred_at=SystemClock().now(),
        ),
    )


async def _reload(world: World, case_id: uuid.UUID) -> ProductDevelopmentCase:
    found = await world.stack.cases.get(_context(frozenset()), ProductDevelopmentCaseId(case_id))
    assert found is not None
    return found


async def _item_coding(world: World, operator: AccessContext) -> uuid.UUID:
    """A case of Alpha at step 9, steps 1-8 saved as their suites save them."""
    cases = world.stack.cases
    case = ProductDevelopmentCase.propose(
        id=ProductDevelopmentCaseId(uuid.uuid4()),
        tenant_id=TenantId(ALPHA),
        workspace_id=WorkspaceId(ALPHA_WS),
        proposal_code=f"DX-{uuid.uuid4().hex[:8]}",
        product_name="Chảo chống dính 26cm",
        category="Chảo",
        actor_id=operator.principal_id,
    )
    await cases.add(operator, case, audit=_audit(operator, case, "propose"))
    case.request_sample(actor_id=operator.principal_id, supplier_name="NCC Minh Long")
    await cases.save(operator, case, audit=_audit(operator, case, "request_sample"))
    case.receive_sample(actor_id=operator.principal_id)
    await cases.save(operator, case, audit=_audit(operator, case, "receive_sample"))
    case = await _reload(world, case.id.value)
    paper = await _document(world, operator, case.id.value, DocumentType.SAMPLE_EVALUATION)
    case.pass_sample(actor_id=operator.principal_id, evaluation=paper)
    await cases.save(operator, case, audit=_audit(operator, case, "pass_sample"))
    case.bod_approve(actor_id=uuid.uuid4())
    await cases.save(operator, case, audit=_audit(operator, case, "bod_approve"))
    case = await _reload(world, case.id.value)
    paper = await _document(world, operator, case.id.value, DocumentType.PRODUCT_PROFILE_BM04)
    case.complete_profile(actor_id=operator.principal_id, profile=paper)
    await cases.save(operator, case, audit=_audit(operator, case, "complete_profile"))
    case = await _reload(world, case.id.value)
    paper = await _document(
        world, operator, case.id.value, DocumentType.SUPPLIER_CONFIRMATION_EMAIL
    )
    case.confirm_with_supplier(actor_id=operator.principal_id, confirmation=paper)
    await cases.save(operator, case, audit=_audit(operator, case, "confirm_with_supplier"))
    return case.id.value


@dataclass
class Submitted:
    case_id: uuid.UUID
    item_code: str
    review: ReviewRaise | None


async def _submitted(
    world: World, operator: AccessContext, *, stack: Stack | None = None
) -> Submitted:
    """Coded through the step command (an item code, one SKU), then submitted."""
    case_id = await _item_coding(world, operator)
    advance = (stack or world.stack).advance
    code = f"MH-{uuid.uuid4().hex[:8]}"
    target = ProductDevelopmentCaseId(case_id)
    await advance.handle(
        operator, case_id=target, action=ProductAction.ISSUE_ITEM_CODE, item_code=code
    )
    await advance.handle(
        operator,
        case_id=target,
        action=ProductAction.ADD_SKU,
        sku=SkuDraft(f"{code}-RED", "Đỏ 26cm", 300),
    )
    result = await advance.handle(operator, case_id=target, action=ProductAction.SUBMIT_FOR_SIGNOFF)
    return Submitted(case_id, code, result.review)


async def _step(world: World, case_id: uuid.UUID) -> ApprovalRequest | None:
    """The sign-off step waiting now: the raiser's bookkeeping read, which no
    audience narrows."""
    found = await world.stack.approvals.raised_by_payload(
        _context(frozenset()),
        approval_type=SIGNOFF_APPROVAL_TYPE,
        key=BOD_REVIEW_CASE_KEY,
        value=str(case_id),
    )
    return found


async def _decide(
    world: World, approval: ApprovalRequest, by: AccessContext, *, approve: bool, comment: str
) -> ApprovalRequest:
    return await world.stack.decisions.decide(
        approval_id=approval.id,
        approve=approve,
        comment=comment,
        context=by,
        authorization=_DECIDE,
    )


def _bod() -> AccessContext:
    return _context(frozenset({"approvals.decide", BOD_SCOPE}))


def _accountant() -> AccessContext:
    return _context(frozenset({"approvals.decide", ACCOUNTING_SCOPE}))


async def _told(world: World, member: AccessContext, item_code: str) -> list[str]:
    """The titles of `member`'s notifications about this sign-off."""
    inbox = NotificationService(SqlNotificationRepository(world.stack.sessions))
    return [n.title for n in (await inbox.latest(member)).items if "Chờ ký" in n.title]


async def _untouched(world: World, submitted: Submitted, approval: ApprovalRequest) -> None:
    assert (await _reload(world, submitted.case_id)).state is ProductDevState.PENDING_SIGNOFF
    still = await _step(world, submitted.case_id)
    assert still is not None and still.id == approval.id
    assert approval.run_id is not None
    run = await world.stack.run_store.get(world.stack.lookup(), approval.run_id)
    assert run.status is RunStatus.WAITING_APPROVAL


# --- the sign-off, end to end ----------------------------------------------------------


async def test_bgd_then_accounting_sign_one_run_and_the_case_is_ready_to_order(
    world: World,
) -> None:
    operator = _context(OPERATOR_SCOPES)
    bod = await _member(world, "sc_bod", "approver")
    accountant = await _member(world, "sc_finance", "approver")

    submitted = await _submitted(world, operator)

    assert submitted.review is ReviewRaise.RAISED
    first = await _step(world, submitted.case_id)
    assert first is not None and first.run_id is not None
    assert first.approval_type == SIGNOFF_APPROVAL_TYPE
    assert first.required_scope == BOD_SCOPE
    assert first.requested_by.value == operator.principal_id
    assert (first.payload["step"], first.payload["step_no"]) == ("bod", 1)
    assert first.payload["item_code"] == submitted.item_code
    # BGĐ is told; Kế toán is not, until its own step exists.
    assert len(await _told(world, bod, submitted.item_code)) == 1
    assert await _told(world, accountant, submitted.item_code) == []

    await _decide(world, first, bod, approve=True, comment="BGĐ đồng ý")

    second = await _step(world, submitted.case_id)
    assert second is not None and second.id != first.id
    assert second.run_id == first.run_id  # the same run, resumed
    assert second.required_scope == ACCOUNTING_SCOPE
    assert (second.payload["step"], second.payload["step_no"]) == ("accounting", 2)
    assert second.requested_by.value == operator.principal_id
    assert (await _reload(world, submitted.case_id)).state is ProductDevState.PENDING_SIGNOFF
    # Raised inside the run, where nobody is told: the lane tells Kế toán.
    await world.stack.lane.run()
    assert len(await _told(world, accountant, submitted.item_code)) == 1
    await world.stack.lane.run()
    assert len(await _told(world, accountant, submitted.item_code)) == 1

    await _decide(world, second, accountant, approve=True, comment="Kế toán đồng ý")

    case = await _reload(world, submitted.case_id)
    assert case.state is ProductDevState.READY_TO_ORDER
    assert await _step(world, submitted.case_id) is None
    run = await world.stack.run_store.get(world.stack.lookup(), first.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None and run.result["outcome"] == "signoff_approve"
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    assert [(t.action, t.actor_id) for t in history[-3:]] == [
        (ProductAction.ADD_SKU, operator.principal_id),
        (ProductAction.SUBMIT_FOR_SIGNOFF, operator.principal_id),
        (ProductAction.SIGNOFF_APPROVE, accountant.principal_id),
    ]
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.audit_events WHERE resource_id = :c"
            " AND action = 'supply_chain.product_case.signoff_step_approved' AND actor_id = :a",
            c=str(case.id),
            a=bod.principal_id,
        )
        == 1
    )


async def test_accounting_not_approving_returns_the_case_to_coding_with_its_codes(
    world: World,
) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    await _decide(world, first, _bod(), approve=True, comment="BGĐ đồng ý")
    second = await _step(world, submitted.case_id)
    assert second is not None
    accountant = _accountant()

    await _decide(world, second, accountant, approve=False, comment="Giá vốn chưa khớp BM04")

    case = await _reload(world, submitted.case_id)
    assert case.state is ProductDevState.ITEM_CODING
    assert case.item_code is not None and case.item_code.code == submitted.item_code
    assert [s.sku_code for s in case.skus] == [f"{submitted.item_code}-RED"]
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    assert (history[-1].action, history[-1].actor_id, history[-1].reason) == (
        ProductAction.SIGNOFF_REJECT,
        accountant.principal_id,
        "Giá vốn chưa khớp BM04",
    )

    # Coded again and resubmitted: a new round, a new run, BGĐ first again.
    again = await world.stack.advance.handle(
        operator,
        case_id=ProductDevelopmentCaseId(submitted.case_id),
        action=ProductAction.SUBMIT_FOR_SIGNOFF,
    )
    assert again.review is ReviewRaise.RAISED
    assert again.case.signoff_round == 2
    resubmitted = await _step(world, submitted.case_id)
    assert resubmitted is not None and resubmitted.run_id != first.run_id
    assert resubmitted.required_scope == BOD_SCOPE


async def test_bgd_not_approving_raises_no_accounting_step(world: World) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None

    await _decide(world, first, _bod(), approve=False, comment="Chưa đủ thông tin giá")

    assert (await _reload(world, submitted.case_id)).state is ProductDevState.ITEM_CODING
    assert await _step(world, submitted.case_id) is None
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.approval_requests"
            " WHERE approval_type = :t AND payload->>'product_dev_case_id' = :c",
            t=SIGNOFF_APPROVAL_TYPE,
            c=str(submitted.case_id),
        )
        == 1
    )


# --- who may decide ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "decider",
    [
        pytest.param(frozenset({"approvals.decide", ACCOUNTING_SCOPE}), id="accounting-on-bgd"),
        pytest.param(frozenset({"approvals.decide"}), id="manager"),
        pytest.param(frozenset(), id="platform_admin"),
    ],
)
async def test_a_decider_without_the_stamped_scope_does_not_find_the_step(
    world: World, decider: frozenset[str]
) -> None:
    """ADR 0020 (QO-8, and the 2026-10-07 amendment): the stamp, not a role,
    decides; `platform_admin` included. A 404, nothing moves."""
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    roles = frozenset({"platform_admin"}) if not decider else frozenset({"manager"})

    for approve in (True, False):
        with pytest.raises(NotFoundError):
            await _decide(
                world, first, _context(decider, roles=roles), approve=approve, comment="tôi ký"
            )
    await _untouched(world, submitted, first)


async def test_bgd_cannot_sign_the_accounting_step(world: World) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    bod = _bod()
    await _decide(world, first, bod, approve=True, comment="BGĐ đồng ý")
    second = await _step(world, submitted.case_id)
    assert second is not None

    with pytest.raises(NotFoundError):
        await _decide(world, second, bod, approve=True, comment="ký luôn")
    await _untouched(world, submitted, second)


async def test_the_submitter_cannot_sign_their_own_submission(world: World) -> None:
    """The strict prefix: the requester decides neither way, even holding
    both scopes; a decision needs a comment."""
    operator = _context(OPERATOR_SCOPES | {"approvals.decide", BOD_SCOPE})
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None

    for approve in (True, False):
        with pytest.raises(ConflictError, match="separation of duties"):
            await _decide(world, first, operator, approve=approve, comment="tự ký")
    with pytest.raises(ConflictError, match="review comment"):
        await _decide(world, first, _bod(), approve=True, comment="  ")
    await _untouched(world, submitted, first)


@pytest.mark.parametrize(
    "tenant",
    [
        pytest.param(ALPHA, id="another-workspace-same-tenant"),
        pytest.param(BETA, id="another-tenant"),
    ],
)
async def test_a_signer_of_another_workspace_or_tenant_finds_no_step(
    world: World, tenant: uuid.UUID
) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    if tenant == ALPHA:
        elsewhere = await _member(
            world, "sc_bod", "approver", workspace=await _another_workspace(world)
        )
        assert {"approvals.decide", BOD_SCOPE} <= elsewhere.scopes
    else:
        elsewhere = _context(
            frozenset({"approvals.decide", BOD_SCOPE}), tenant=tenant, workspace=uuid.uuid4()
        )

    with pytest.raises(NotFoundError):
        await _decide(world, first, elsewhere, approve=True, comment="Đồng ý")
    total, listed = await world.stack.approvals.list_pending_by_type_prefix(
        elsewhere, prefix=APPROVAL_TYPE_PREFIX, limit=50
    )
    assert (total, listed) == (0, [])
    await _untouched(world, submitted, first)


async def test_the_role_catalogue_gives_kế_toán_the_accounting_stamp(world: World) -> None:
    """`sc_finance` signs Kế toán's step (no `sc_accounting`, QE-16): a real
    member holding it and a platform approval role finds and decides it."""
    operator = _context(OPERATOR_SCOPES)
    accountant = await _member(world, "sc_finance", "approver")
    assert ACCOUNTING_SCOPE in accountant.scopes
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    await _decide(world, first, _bod(), approve=True, comment="BGĐ đồng ý")
    second = await _step(world, submitted.case_id)
    assert second is not None

    decided = await _decide(world, second, accountant, approve=True, comment="Đã kiểm giá vốn")

    assert decided.status is ApprovalStatus.APPROVED
    assert (await _reload(world, submitted.case_id)).state is ProductDevState.READY_TO_ORDER


# --- the order is the tenant's, stamped at submission -------------------------------------


async def test_a_tenant_order_reaches_submissions_after_it_only(world: World) -> None:
    operator = _context(OPERATOR_SCOPES)
    before = await _submitted(world, operator)
    override = load_supply_chain_product_approvals(_APPROVALS).model_dump(mode="json")
    override["signoff"] = list(reversed(override["signoff"]))
    admin = _context(frozenset())
    await world.stack.policies.put(
        admin,
        PRODUCT_APPROVALS_POLICY_ID,
        override,
        audit=AuditEvent(
            id=uuid.uuid4(),
            tenant_id=TenantId(ALPHA),
            workspace_id=WorkspaceId(ALPHA_WS),
            actor_id=UserId(admin.principal_id),
            action="supply_chain.product_approvals.override_set",
            resource_type="product_approvals",
            resource_id=PRODUCT_APPROVALS_POLICY_ID,
            occurred_at=SystemClock().now(),
        ),
    )
    try:
        after = await _submitted(world, operator)

        waiting_before = await _step(world, before.case_id)
        waiting_after = await _step(world, after.case_id)
        assert waiting_before is not None and waiting_after is not None
        assert waiting_before.required_scope == BOD_SCOPE
        assert waiting_after.required_scope == ACCOUNTING_SCOPE
        await _decide(world, waiting_after, _accountant(), approve=True, comment="ok")
        next_after = await _step(world, after.case_id)
        assert next_after is not None and next_after.required_scope == BOD_SCOPE
        # The case submitted first keeps BGĐ-then-Kế toán.
        await _decide(world, waiting_before, _bod(), approve=True, comment="ok")
        next_before = await _step(world, before.case_id)
        assert next_before is not None and next_before.required_scope == ACCOUNTING_SCOPE
    finally:
        async with world.migrator.begin() as conn:
            await conn.execute(
                sa.text(
                    "DELETE FROM platform.policy_overrides WHERE tenant_id = :t AND policy_id = :p"
                ),
                {"t": ALPHA, "p": PRODUCT_APPROVALS_POLICY_ID},
            )


# --- a case that moved on while a step was decided ----------------------------------------


@pytest.mark.parametrize("at_step", [1, 2])
async def test_cancelled_while_a_step_waits_the_decision_is_superseded(
    world: World, at_step: int
) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    waiting = await _step(world, submitted.case_id)
    assert waiting is not None and waiting.run_id is not None
    if at_step == 2:
        await _decide(world, waiting, _bod(), approve=True, comment="BGĐ đồng ý")
        waiting = await _step(world, submitted.case_id)
        assert waiting is not None
    await world.stack.advance.handle(
        operator,
        case_id=ProductDevelopmentCaseId(submitted.case_id),
        action=ProductAction.CANCEL,
        reason="Dự án dừng",
    )

    decider = _bod() if at_step == 1 else _accountant()
    await _decide(world, waiting, decider, approve=True, comment="Đồng ý")

    case = await _reload(world, submitted.case_id)
    assert case.state is ProductDevState.CANCELLED
    assert await _step(world, submitted.case_id) is None
    assert waiting.run_id is not None
    run = await world.stack.run_store.get(world.stack.lookup(), waiting.run_id)
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None and run.result["outcome"] == SUPERSEDED
    assert (
        await _count(
            world,
            "SELECT count(*) FROM platform.audit_events WHERE resource_id = :c"
            " AND action = 'supply_chain.product_case.signoff_superseded'",
            c=str(case.id),
        )
        == 1
    )


# --- raised later by the lane --------------------------------------------------------------


async def test_a_refused_start_keeps_the_submission_and_the_lane_raises_the_signoff(
    world: World,
) -> None:
    operator = _context(OPERATOR_SCOPES)
    refusing = Stack(world.urls.app, allowance=_NoRunsLeft())
    try:
        submitted = await _submitted(world, operator, stack=refusing)
    finally:
        await refusing.dispose()
    assert submitted.review is ReviewRaise.NOT_RAISED
    assert (await _reload(world, submitted.case_id)).state is ProductDevState.PENDING_SIGNOFF
    assert await _step(world, submitted.case_id) is None

    await world.stack.lane.run()

    raised = await _step(world, submitted.case_id)
    assert raised is not None
    assert raised.requested_by.value == operator.principal_id
    assert raised.required_scope == BOD_SCOPE


# --- deciding on Zalo after a view (ADR 0014) ---------------------------------------------


async def test_accounting_signs_on_zalo_after_a_view_and_the_signoff_applies(
    world: World,
) -> None:
    operator = _context(OPERATOR_SCOPES)
    submitted = await _submitted(world, operator)
    first = await _step(world, submitted.case_id)
    assert first is not None
    await _decide(world, first, _bod(), approve=True, comment="BGĐ đồng ý")
    second = await _step(world, submitted.case_id)
    assert second is not None
    accountant = await _member(world, "sc_finance", "approver")
    chat = await _linked(world, accountant)
    zalo = _zalo(world)

    code = await _code(zalo, accountant, second.id, "Kế toán đã kiểm, đồng ý.")
    outcome = await _send(zalo, accountant, chat, code)

    assert outcome.decided is True, outcome
    case = await _reload(world, submitted.case_id)
    assert case.state is ProductDevState.READY_TO_ORDER
    history = await world.stack.cases.list_transitions(_context(frozenset()), case.id)
    assert (history[-1].action, history[-1].actor_id) == (
        ProductAction.SIGNOFF_APPROVE,
        accountant.principal_id,
    )
