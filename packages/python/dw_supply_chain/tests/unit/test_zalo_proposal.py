"""Unit: the Zalo proposal command, over fakes that keep the database's promises.

`FakeDrafts` keeps one draft per person and channel with the repository's
version guard; `FakeCases.add` consumes a draft only at its summarised,
unexpired version and refuses a taken code — the two refusals the SQL adapter
makes. `ProposeProductCase` and `ScopeAuthorizationService` are the real ones,
so a ceiling that disagreed with what the handler enforces would show here.
The model is a script, validated by the real schema as the gateway does.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelOutputInvalidError, ModelRequest
from dw_kernel.channels import chat_reference
from dw_kernel.errors import ConflictError, InfrastructureError, QuotaExceededError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import (
    PRODUCT_CASE_READ,
    PRODUCT_CASE_WRITE,
    duty_scope,
)
from dw_supply_chain.application.product_cases import ProposeProductCase
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.domain.product_proposal import (
    DRAFT_TTL,
    DraftClaim,
    ProposalDraft,
    ProposalDraftChangedError,
    ProposalField,
)
from dw_supply_chain.presentation.zalo_proposal import (
    ALREADY_HANDLED,
    BUDGET_SPENT,
    CANCELLED,
    CONFIRM_HINT,
    EXPIRED,
    NOT_UNDERSTOOD,
    NOTHING_TO_CONFIRM,
    PROPOSAL_CEILING,
    RESUMMARIZED,
    ZaloProposalCommand,
    duplicate_code,
    no_permission,
    quota_spent,
)
from dw_supply_chain.product_action_duties import (
    PRODUCT_ACTION_DUTIES_POLICY_ID,
    SupplyChainProductActionDuties,
)

pytestmark = pytest.mark.unit

TENANT, WORKSPACE = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
ORDERING = duty_scope(CaseDuty.ORDERING)
EXCEPTIONS = duty_scope(CaseDuty.EXCEPTIONS)
PROPOSER = frozenset({PRODUCT_CASE_READ, PRODUCT_CASE_WRITE, ORDERING})
CHAT = "zalo-chat-1"
MESSAGE = "đề xuất SP chảo chống dính 28cm, mã CH-28, nhóm Chảo"
FULL = {
    "kind": "propose_product",
    "proposal_code": "CH-28",
    "product_name": "chảo chống dính 28cm",
    "category": "Chảo",
}

DUTIES = SupplyChainProductActionDuties.model_validate(
    {
        "schema_version": "1.0",
        "policy_id": PRODUCT_ACTION_DUTIES_POLICY_ID,
        "policy_version": "1.0.0",
        "action_duties": {
            "propose": "ordering",
            "request_sample": "ordering",
            "receive_sample": "rnd",
            "pass_sample": "rnd",
            "request_revision": "rnd",
            "receive_revised_sample": "rnd",
            "reject_sample": "rnd",
            "wait_for_external": "exceptions",
            "flag_blocked": "exceptions",
            "flag_manual_review": "exceptions",
            "resume": "exceptions",
            "cancel": "ordering",
        },
    }
)


@dataclass(frozen=True)
class Message:
    text: str
    channel: str = "zalo"
    chat_id: str = CHAT
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass
class FakeDrafts:
    """`ProposalDraftRepositoryPort`: one draft per (tenant, workspace, person,
    channel), replaced only at the version the caller read."""

    rows: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID, str], ProposalDraft] = field(
        default_factory=dict
    )

    @staticmethod
    def key(context: AccessContext, channel: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
        return (context.tenant_id, context.workspace_id, context.principal_id, channel)

    async def open_draft(self, context: AccessContext, channel: str) -> ProposalDraft | None:
        return self.rows.get(self.key(context, channel))

    async def put(
        self,
        context: AccessContext,
        channel: str,
        *,
        fields: Mapping[ProposalField, str],
        draft_version: int,
        summarized_version: int | None,
        expires_at: datetime,
        previous_version: int | None,
    ) -> ProposalDraft:
        key = self.key(context, channel)
        existing = self.rows.get(key)
        if (existing is None) != (previous_version is None) or (
            existing is not None and existing.draft_version != previous_version
        ):
            raise ProposalDraftChangedError("changed")
        draft = ProposalDraft(
            id=existing.id if existing is not None else uuid.uuid4(),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            user_id=context.principal_id,
            channel=channel,
            fields=dict(fields),
            draft_version=draft_version,
            summarized_version=summarized_version,
            expires_at=expires_at,
        )
        self.rows[key] = draft
        return draft

    async def discard(self, context: AccessContext, channel: str) -> bool:
        return self.rows.pop(self.key(context, channel), None) is not None


@dataclass
class FakeCases:
    """`ProductCaseRepositoryPort.add` as the SQL adapter keeps it: the draft is
    consumed only at its summarised, unexpired version, in the same step that
    stores the case; a taken code is a conflict and changes nothing."""

    drafts: FakeDrafts
    clock: FixedClock
    taken_codes: set[str] = field(default_factory=set)
    added: list[tuple[AccessContext, ProductDevelopmentCase, AuditEvent]] = field(
        default_factory=list
    )

    async def add(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        *,
        audit: AuditEvent,
        consume: DraftClaim | None = None,
    ) -> None:
        if consume is not None:
            key = next((k for k, d in self.drafts.rows.items() if d.id == consume.draft_id), None)
            draft = self.drafts.rows.get(key) if key is not None else None
            if (
                draft is None
                or key != FakeDrafts.key(context, draft.channel)
                or draft.draft_version != consume.draft_version
                or draft.summarized_version != consume.draft_version
                or draft.expired(self.clock.now())
            ):
                raise ProposalDraftChangedError("gone")
        if case.proposal_code in self.taken_codes:
            raise ConflictError("mã đề xuất này đã có trong tenant")
        if consume is not None:
            del self.drafts.rows[key]  # type: ignore[arg-type]
        self.taken_codes.add(case.proposal_code)
        self.added.append((context, case, audit))

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("not exercised by the proposal command")

    async def save(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("not exercised by the proposal command")

    async def list_page(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("not exercised by the proposal command")

    async def list_transitions(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("not exercised by the proposal command")

    async def list_rounds(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("not exercised by the proposal command")


@dataclass
class FakePolicies:
    stored: dict[str, Mapping[str, object]] = field(default_factory=dict)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        found = self.stored.get(policy_id)
        return dict(found) if found is not None else None

    async def put(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("not exercised by the proposal command")


@dataclass
class ScriptedModel:
    """Answers each call with the next scripted answer, validated by the real
    schema as `RoutingModelGateway` does; an exception in the script is raised."""

    answers: list[object] = field(default_factory=list)
    calls: list[RunContext] = field(default_factory=list)

    async def generate_structured[T: BaseModel](
        self, request: ModelRequest, output_type: type[T], *, run_context: RunContext
    ) -> T:
        self.calls.append(run_context)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if answer == "hang":
            # A slow provider that WOULD answer: only the timeout stops it.
            await asyncio.sleep(1)
            answer = FULL
        try:
            return output_type.model_validate(answer)
        except ValidationError as exc:
            raise ModelOutputInvalidError("model output failed schema validation") from exc


@dataclass
class FakeWorkspaces:
    async def name_of(self, context: AccessContext) -> str | None:
        return "Cung ứng HN"


@dataclass
class Bench:
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    drafts: FakeDrafts = field(default_factory=FakeDrafts)
    model: ScriptedModel = field(default_factory=ScriptedModel)
    policies: FakePolicies = field(default_factory=FakePolicies)
    cases: FakeCases = field(init=False)
    replies: list[str] = field(default_factory=list)
    principal: uuid.UUID = field(default_factory=uuid.uuid4)

    def __post_init__(self) -> None:
        self.cases = FakeCases(self.drafts, self.clock)

    def command(self, *, timeout: float = 20.0) -> ZaloProposalCommand:
        return ZaloProposalCommand(
            propose=ProposeProductCase(
                repo=self.cases,
                authz=ScopeAuthorizationService(),
                policy_override_repo=self.policies,
                platform_default_duties=DUTIES,
                ids=Uuid4Generator(),
                clock=self.clock,
            ),
            drafts=self.drafts,
            gateway=self.model,
            workspaces=FakeWorkspaces(),
            ids=Uuid4Generator(),
            clock=self.clock,
            web_url="https://portal.example/",
            model_timeout_seconds=timeout,
        )

    def context(self, scopes: frozenset[str] = PROPOSER) -> AccessContext:
        # As the router hands it over: cut to the command's ceiling, no role.
        return AccessContext(
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            principal_id=self.principal,
            roles=frozenset(),
            scopes=scopes & PROPOSAL_CEILING,
            plan_id="professional",
        )

    async def say(
        self, text: str, *answers: object, scopes: frozenset[str] = PROPOSER, timeout: float = 20.0
    ) -> str:
        self.model.answers.extend(answers)

        async def reply(sent: str) -> None:
            self.replies.append(sent)

        handled = await self.command(timeout=timeout).handle(
            Message(text), self.context(scopes), reply
        )
        assert handled
        return self.replies[-1]

    def draft(self) -> ProposalDraft | None:
        return next(iter(self.drafts.rows.values()), None)


# ---- the happy path, and what it records -----------------------------------------


async def test_a_full_proposal_is_summarised_then_created_on_dong_y() -> None:
    bench = Bench()
    summary = await bench.say(MESSAGE, FULL)
    assert "Mã đề xuất: CH-28" in summary and "Category: Chảo" in summary
    assert "«Cung ứng HN»" in summary and CONFIRM_HINT in summary
    draft = bench.draft()
    assert draft is not None and draft.awaits_confirmation
    assert bench.cases.added == []

    created = await bench.say("dong y")  # no accents, lower case: still agreement

    [(_context, case, audit)] = bench.cases.added
    assert (case.proposal_code, case.product_name, case.category) == (
        "CH-28",
        "chảo chống dính 28cm",
        "Chảo",
    )
    assert case.pic_user_id == bench.principal
    assert (case.tenant_id.value, case.workspace_id.value) == (TENANT, WORKSPACE)
    assert "anh/chị là PIC" in created
    assert f"https://portal.example/supply-chain/product-cases/{case.id.value}" in created
    assert bench.draft() is None
    # The audit names the channel and a reference to the chat, never the chat.
    assert audit.details["origin"] == {"channel": "zalo", "chat_ref": chat_reference("zalo", CHAT)}
    assert CHAT not in str(audit.details)
    assert len(bench.model.calls) == 1  # "Đồng ý" spends no model call


async def test_the_command_acts_with_exactly_the_scopes_propose_needs() -> None:
    """The membership holds approvals.decide and every duty; the case is created
    with a context holding the write and the ordering duty, nothing else."""
    bench = Bench()
    everything = PROPOSER | {"approvals.decide", EXCEPTIONS, duty_scope(CaseDuty.QC)}
    await bench.say(MESSAGE, FULL, scopes=everything)
    await bench.say("Đồng ý", scopes=everything)
    [(context, _case, _audit)] = bench.cases.added
    assert context.scopes == {PRODUCT_CASE_WRITE, ORDERING}
    assert context.roles == frozenset()
    assert "approvals.decide" not in PROPOSAL_CEILING


async def test_the_ceiling_follows_the_tenants_own_duty_policy() -> None:
    """A tenant whose policy gives `propose` to the exceptions duty: an ordering
    holder is refused, an exceptions holder proposes — the ceiling is the
    handler's own answer, not a list kept beside it."""
    override = DUTIES.model_dump(mode="json")
    override["action_duties"]["propose"] = "exceptions"
    bench = Bench(policies=FakePolicies({PRODUCT_ACTION_DUTIES_POLICY_ID: override}))

    refused = await bench.say(MESSAGE, scopes=PROPOSER)
    assert refused == no_permission("Cung ứng HN")
    assert bench.model.calls == [] and bench.draft() is None

    holder = frozenset({PRODUCT_CASE_WRITE, EXCEPTIONS})
    await bench.say(MESSAGE, FULL, scopes=holder)
    await bench.say("Đồng ý", scopes=holder)
    [(context, _case, _audit)] = bench.cases.added
    assert context.scopes == {PRODUCT_CASE_WRITE, EXCEPTIONS}


async def test_someone_who_may_not_propose_is_refused_without_a_draft_or_a_model_call() -> None:
    bench = Bench()
    reply = await bench.say(MESSAGE, scopes=frozenset({PRODUCT_CASE_READ, PRODUCT_CASE_WRITE}))
    assert reply == no_permission("Cung ứng HN")
    assert bench.model.calls == [] and bench.draft() is None and bench.cases.added == []


# ---- several turns -----------------------------------------------------------------


async def test_missing_fields_are_asked_for_until_the_proposal_is_complete() -> None:
    bench = Bench()
    asked = await bench.say(
        "đề xuất SP chảo chống dính 28cm, mã CH-28",
        {
            "kind": "propose_product",
            "proposal_code": "CH-28",
            "product_name": "chảo chống dính 28cm",
        },
    )
    assert asked == "Anh/chị gửi thêm Category giúp mình."
    draft = bench.draft()
    assert draft is not None and draft.summarized_version is None

    summary = await bench.say("nhóm Chảo", {"kind": "amend", "category": "Chảo"})
    assert "Category: Chảo" in summary
    draft = bench.draft()
    assert draft is not None and draft.draft_version == 2 and draft.awaits_confirmation


async def test_a_value_not_in_the_message_is_dropped_and_asked_again() -> None:
    """Missing evidence: no Category in the message, the model fills one."""
    bench = Bench()
    reply = await bench.say(
        "đề xuất SP chảo chống dính 28cm, mã CH-28", {**FULL, "category": "Nồi"}
    )
    assert "Mình không thấy Category trong tin" in reply
    assert reply.endswith("Anh/chị gửi thêm Category giúp mình.")
    draft = bench.draft()
    assert draft is not None and ProposalField.CATEGORY not in draft.fields


async def test_a_change_after_the_summary_is_summarised_again() -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    resummary = await bench.say(
        "sửa tên thành chảo chống dính 30cm",
        {"kind": "amend", "product_name": "chảo chống dính 30cm"},
    )
    assert "Tên sản phẩm: chảo chống dính 30cm" in resummary
    draft = bench.draft()
    assert draft is not None and draft.draft_version == 2 and draft.summarized_version == 2


async def test_dong_y_on_a_version_nobody_was_shown_creates_nothing_and_shows_it() -> None:
    """The draft moved past its summary without a new one (as a refused code
    does): "Đồng ý" re-summarises instead of creating."""
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    draft = bench.draft()
    assert draft is not None
    key = next(iter(bench.drafts.rows))
    bench.drafts.rows[key] = replace(draft, draft_version=2, summarized_version=1)

    reply = await bench.say("Đồng ý")

    assert bench.cases.added == []
    assert reply.startswith(RESUMMARIZED)
    again = bench.draft()
    assert again is not None and again.awaits_confirmation and again.draft_version == 2
    await bench.say("Đồng ý")
    assert len(bench.cases.added) == 1


@pytest.mark.parametrize("text", ["ok", "được", "OK nhé"])
async def test_ok_and_duoc_do_not_create(text: str) -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    reply = await bench.say(text, {"kind": "unsupported"})
    assert reply == f"{NOT_UNDERSTOOD} {CONFIRM_HINT}"
    assert bench.cases.added == []


async def test_no_dong_y_means_no_case() -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    assert bench.cases.added == []
    assert await Bench().say("Đồng ý") == NOTHING_TO_CONFIRM


async def test_bo_de_xuat_deletes_the_draft() -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    assert await bench.say("Bỏ đề xuất") == CANCELLED
    assert bench.draft() is None
    assert await bench.say("Đồng ý") == NOTHING_TO_CONFIRM
    assert bench.cases.added == []


async def test_an_expired_draft_is_not_created_and_says_so() -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    bench.clock.advance_to(NOW + DRAFT_TTL + timedelta(minutes=1))
    assert await bench.say("Đồng ý") == EXPIRED
    assert bench.cases.added == [] and bench.draft() is None


async def test_a_taken_code_is_dropped_and_a_new_one_asked_for() -> None:
    bench = Bench()
    bench.cases.taken_codes.add("CH-28")
    await bench.say(MESSAGE, FULL)
    assert await bench.say("Đồng ý") == duplicate_code("CH-28")
    draft = bench.draft()
    assert draft is not None and ProposalField.PROPOSAL_CODE not in draft.fields
    assert not draft.awaits_confirmation
    assert await bench.say("Đồng ý") == "Anh/chị gửi thêm mã đề xuất giúp mình."
    assert bench.cases.added == []


async def test_a_second_dong_y_creates_nothing() -> None:
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    await bench.say("Đồng ý")
    assert await bench.say("Đồng ý") == NOTHING_TO_CONFIRM
    assert len(bench.cases.added) == 1


async def test_a_dong_y_whose_draft_was_consumed_meanwhile_creates_nothing() -> None:
    """Both deliveries read the draft before either created: the guarded
    consume refuses the second."""
    bench = Bench()
    await bench.say(MESSAGE, FULL)
    draft = bench.draft()
    assert draft is not None
    stale = replace(draft)

    async def open_stale(context: AccessContext, channel: str) -> ProposalDraft | None:
        return stale

    await bench.say("Đồng ý")
    object.__setattr__(bench.drafts, "open_draft", open_stale)
    assert await bench.say("Đồng ý") == ALREADY_HANDLED
    assert len(bench.cases.added) == 1


# ---- the model misbehaving or unavailable --------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        {"kind": "propose_product", "pic_user_id": str(uuid.uuid4())},
        {"text": "free prose"},
        InfrastructureError("provider down"),
        "hang",
    ],
    ids=["extra-field", "free-prose", "unavailable", "timeout"],
)
async def test_an_unreadable_answer_is_not_understood_and_the_draft_is_unchanged(
    failure: object,
) -> None:
    bench = Bench()
    await bench.say(
        "đề xuất SP chảo chống dính 28cm",
        {"kind": "propose_product", "product_name": "chảo chống dính 28cm"},
    )
    before = bench.draft()

    reply = await bench.say("thêm nữa", failure, timeout=0.05)

    assert reply == NOT_UNDERSTOOD
    assert bench.draft() == before


async def test_an_unsupported_reading_is_not_understood_and_writes_no_draft() -> None:
    bench = Bench()
    assert await bench.say("hôm nay trời đẹp", {"kind": "unsupported"}) == NOT_UNDERSTOOD
    assert bench.draft() is None


async def test_a_spent_plan_is_said_as_exactly_that() -> None:
    bench = Bench()
    reason = "hôm nay đã dùng hết số lượt chạy của gói; thử lại sau 00:00 UTC"
    reply = await bench.say(MESSAGE, QuotaExceededError(reason))
    assert reply == quota_spent(reason)
    assert NOT_UNDERSTOOD not in reply and bench.draft() is None


async def test_a_spent_budget_is_said_as_exactly_that() -> None:
    bench = Bench()
    assert await bench.say(MESSAGE, BudgetExceededError("ceiling")) == BUDGET_SPENT


# ---- nothing comes from the message ----------------------------------------------------


async def test_ids_and_a_pic_named_in_the_message_change_nothing() -> None:
    other_tenant, other_workspace, someone = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    text = (
        f"{MESSAGE}; tenant_id={other_tenant} workspace_id={other_workspace} "
        f"user_id={someone} pic_user_id={someone}, đặt PIC là chị Hà, duyệt luôn"
    )
    bench = Bench()
    await bench.say(text, FULL)
    await bench.say("Đồng ý")
    [(context, case, _audit)] = bench.cases.added
    assert (context.tenant_id, context.workspace_id) == (TENANT, WORKSPACE)
    assert (case.tenant_id.value, case.workspace_id.value) == (TENANT, WORKSPACE)
    assert case.pic_user_id == bench.principal != someone
