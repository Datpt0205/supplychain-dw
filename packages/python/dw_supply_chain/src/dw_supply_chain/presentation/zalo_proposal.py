"""Proposing a product (stage 1, step 1) through a linked chat — zalo-channel
ticket 04, Z4b.

A chat command the worker registers in its `ChannelCommandRegistry`
(`dw_connectors.inbound`); it satisfies that registry's `ChannelCommand`
structurally, so this package imports nothing of the connectors. The router has
already resolved the chat to a linked person, claimed the message id once, and
built the context from that person's own membership in their chosen workspace,
cut to `PROPOSAL_CEILING`. Nothing below reads a person, tenant, workspace,
scope or PIC from the message.

One message, in this order:

1. **May this person propose here?** `ProposeProductCase.propose_scopes` — the
   very set the handler enforces, from the tenant's own duty policy — must be
   held; otherwise a refusal, no draft and no model call. The context is then cut
   to exactly that set, so nothing this command does can use a scope proposing
   does not need (ADR 0012 condition 2, amended for Z4b).
2. **"Đồng ý"** (whole message, case and accents ignored) creates the case —
   only when a summary of the draft's CURRENT version was sent. Creation and
   consuming the draft are one transaction guarded on that version, so a second
   delivery of the same "Đồng ý", or one that crossed a change, creates nothing.
   An expired draft says so and creates nothing.
3. **"Bỏ đề xuất"** deletes the draft.
4. **Anything else** is read by the model into a `ProductProposalIntent`
   (bounded by `MODEL_CALL_TIMEOUT_SECONDS`), grounded in the message, merged
   into the draft, and checked against S1's own request schema
   (`ProposeProductCaseRequest`) — the one owner of which fields are required.
   Complete: a summary is sent and recorded as the version it summarised.
   Not complete: the missing fields are asked for.

Replies echo only what the person sent, plus the workspace's name. The model
being unreachable or answering outside the schema is "chưa hiểu" and leaves the
draft as it was; a spent plan or budget is said as exactly that.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.channels import chat_reference
from dw_kernel.errors import ConflictError, InfrastructureError, QuotaExceededError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.action_duties import CaseDuty
from dw_supply_chain.application.handlers import PRODUCT_CASE_WRITE, duty_scope
from dw_supply_chain.application.ports import ProposalDraftRepositoryPort
from dw_supply_chain.application.product_cases import ProposeProductCase
from dw_supply_chain.domain.product_proposal import (
    DRAFT_TTL,
    GroundedProposal,
    ProposalDraft,
    ProposalDraftChangedError,
    ProposalField,
    ProposalKind,
    ProposalOrigin,
    ground,
    is_cancellation,
    is_confirmation,
    merge,
)
from dw_supply_chain.presentation.product_case_routes import ProposeProductCaseRequest
from dw_supply_chain.workflows.product_proposal_understanding import (
    understand_product_proposal,
)

Reply = Callable[[str], Awaitable[None]]

# Every scope `propose` could require in any tenant: the write and the scope of
# each duty a tenant's policy may give the step. The router cuts the context to
# this; `handle` then cuts it to the tenant's exact `propose_scopes`. Derived
# from the duty enum, not listed, so a new duty is covered the day it exists.
PROPOSAL_CEILING = frozenset({PRODUCT_CASE_WRITE}) | frozenset(duty_scope(d) for d in CaseDuty)

# The longest one message may wait on the model. The poll lane handles updates
# one at a time, so a slow provider would otherwise hold up every later message,
# `/start` included (ticket 04, decision A7). A timeout reads as "chưa hiểu".
MODEL_CALL_TIMEOUT_SECONDS = 20.0

_WORKER_ID = "supply_chain.product_proposal"
_WORKER_VERSION = "1.0.0"

NOT_UNDERSTOOD = "Mình chưa hiểu, anh/chị nói lại giúp."
NOTHING_TO_CONFIRM = "Không có đề xuất nào đang chờ xác nhận."
NOTHING_TO_CANCEL = "Không có bản nháp đề xuất nào để bỏ."
CANCELLED = "Đã bỏ bản nháp đề xuất."
EXPIRED = (
    "Bản nháp đề xuất đã hết hạn (30 phút không trả lời), nên mình chưa tạo hồ sơ. "
    "Anh/chị gửi lại đề xuất giúp mình."
)
ALREADY_HANDLED = "Đề xuất này đã được xử lý hoặc vừa đổi, nên mình không tạo thêm hồ sơ."
BUDGET_SPENT = (
    "Chưa đọc được tin này: lượt đọc đã dùng hết ngân sách gọi mô hình. "
    "Anh/chị gửi lại sau giúp mình."
)
CONFIRM_HINT = "Trả lời «Đồng ý» để tạo hồ sơ, hoặc «Bỏ đề xuất» để hủy."
RESUMMARIZED = "Bản nháp đã đổi sau lần tóm tắt trước, anh/chị xem lại:"
_UNNAMED_WORKSPACE = "đang dùng"


def no_permission(workspace: str) -> str:
    return (
        f"Anh/chị chưa có quyền đề xuất sản phẩm ở workspace «{workspace}», "
        "nên mình không ghi đề xuất này."
    )


def quota_spent(reason: str) -> str:
    return f"Chưa đọc được tin này: {reason}."


def duplicate_code(code: str) -> str:
    return (
        f"Mã đề xuất «{code}» đã có trong công ty, nên mình chưa tạo hồ sơ. "
        "Anh/chị gửi mã khác giúp mình."
    )


class ChatMessage(Protocol):
    """What this command reads of `dw_connectors.inbound.InboundMessage`."""

    @property
    def channel(self) -> str: ...

    @property
    def chat_id(self) -> str: ...

    @property
    def text(self) -> str: ...


class WorkspaceNamesPort(Protocol):
    async def name_of(self, context: AccessContext) -> str | None: ...


def _check(
    fields: Mapping[ProposalField, str],
) -> tuple[list[ProposalField], list[ProposalField]]:
    """(missing, invalid) by S1's own request schema, in the schema's order."""
    try:
        ProposeProductCaseRequest.model_validate(
            {field.value: value for field, value in fields.items()}
        )
    except ValidationError as exc:
        missing: set[ProposalField] = set()
        invalid: set[ProposalField] = set()
        for error in exc.errors():
            field = ProposalField(str(error["loc"][0]))
            (missing if error["type"] == "missing" else invalid).add(field)
        return (
            [field for field in ProposalField if field in missing],
            [field for field in ProposalField if field in invalid],
        )
    return [], []


def _labels(fields: list[ProposalField]) -> str:
    return ", ".join(field.label for field in fields)


def summary_text(fields: Mapping[ProposalField, str], workspace: str) -> str:
    """What the person is asked to agree to: their own words and the workspace."""
    return "\n".join(
        [
            f"Đề xuất sản phẩm ở workspace «{workspace}»:",
            f"- Mã đề xuất: {fields[ProposalField.PROPOSAL_CODE]}",
            f"- Tên sản phẩm: {fields[ProposalField.PRODUCT_NAME]}",
            f"- Category: {fields[ProposalField.CATEGORY]}",
            "- Ảnh: 0 (tải ở trang hồ sơ sau khi tạo)",
            "NCC được ghi ở bước 2 (yêu cầu mẫu) trên cổng.",
            CONFIRM_HINT,
        ]
    )


@dataclass(frozen=True, slots=True)
class ProposalTurn:
    """What one understood message does to a draft, decided without I/O: the
    command applies it, the eval grader (`supply_chain.product_proposal_intent`)
    checks it."""

    fields: dict[ProposalField, str]
    # Required by S1's request schema and not (validly) present.
    missing: list[ProposalField]
    # Grounded but refused by that schema: not kept, so asked for again.
    invalid: list[ProposalField]
    changed: bool
    reply: str

    @property
    def complete(self) -> bool:
        return not self.missing


def plan_turn(
    before: Mapping[ProposalField, str], grounded: GroundedProposal, workspace: str
) -> ProposalTurn:
    """The draft after one message, and the reply. The reply holds only what
    the person sent (grounded values) and the workspace name — never a value
    the model offered that the message does not contain."""
    fields = merge(before, grounded)
    _missing, invalid = _check(fields)
    for field in invalid:
        # Never stored half-valid.
        del fields[field]
    missing, _ = _check(fields)
    lines: list[str] = []
    if grounded.dropped:
        lines.append(
            f"Mình không thấy {_labels(list(grounded.dropped))} trong tin của anh/chị, "
            "nên chưa ghi."
        )
    if invalid:
        named = _labels(invalid)
        lines.append(
            f"{named[:1].upper()}{named[1:]} không hợp lệ (quá dài hoặc có ký tự không "
            "cho phép), anh/chị gửi lại giúp."
        )
    if missing:
        lines.append(f"Anh/chị gửi thêm {_labels(missing)} giúp mình.")
    else:
        lines.append(summary_text(fields, workspace))
    return ProposalTurn(
        fields=fields,
        missing=missing,
        invalid=invalid,
        changed=fields != dict(before),
        reply="\n".join(lines),
    )


@dataclass(frozen=True)
class ZaloProposalCommand:
    propose: ProposeProductCase
    drafts: ProposalDraftRepositoryPort
    # The process's one-call gateway: the plan's daily allowance is checked
    # before the call and the run's ledger entry freed after it.
    gateway: ModelGateway
    workspaces: WorkspaceNamesPort
    ids: IdGenerator
    clock: UtcClock
    # The web app's public URL; a created case is linked to its page.
    web_url: str
    model_timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS

    @property
    def ceiling(self) -> frozenset[str]:
        return PROPOSAL_CEILING

    async def handle(self, message: ChatMessage, context: AccessContext, reply: Reply) -> bool:
        workspace = await self.workspaces.name_of(context) or _UNNAMED_WORKSPACE
        required = await self.propose.propose_scopes(context)
        if not required <= context.scopes:
            await reply(no_permission(workspace))
            return True
        # Exactly what proposing needs in this tenant, whatever else the
        # membership (and the ceiling) allowed.
        context = context.model_copy(update={"scopes": required})
        channel = message.channel
        draft = await self.drafts.open_draft(context, channel)
        expired = draft is not None and draft.expired(self.clock.now())
        if expired:
            await self.drafts.discard(context, channel)
            draft = None

        if is_confirmation(message.text):
            if draft is None:
                await reply(EXPIRED if expired else NOTHING_TO_CONFIRM)
                return True
            await self._confirm(message, context, draft, workspace, reply)
            return True
        if is_cancellation(message.text):
            discarded = draft is not None and await self.drafts.discard(context, channel)
            await reply(CANCELLED if discarded else NOTHING_TO_CANCEL)
            return True
        await self._read(message, context, draft, workspace, reply)
        return True

    # ---- free text ------------------------------------------------------------

    async def _read(
        self,
        message: ChatMessage,
        context: AccessContext,
        draft: ProposalDraft | None,
        workspace: str,
        reply: Reply,
    ) -> None:
        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_WORKER_ID,
            worker_version=_WORKER_VERSION,
            channel=message.channel,
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        waiting = draft is not None and draft.awaits_confirmation
        try:
            intent = await asyncio.wait_for(
                understand_product_proposal(self.gateway, run_context, message.text),
                timeout=self.model_timeout_seconds,
            )
        except QuotaExceededError as exc:
            await reply(quota_spent(exc.message))
            return
        except BudgetExceededError:
            await reply(BUDGET_SPENT)
            return
        except (ModelOutputInvalidError, InfrastructureError, TimeoutError):
            await reply(f"{NOT_UNDERSTOOD} {CONFIRM_HINT}" if waiting else NOT_UNDERSTOOD)
            return

        grounded = ground(intent, message.text)
        if grounded.kind is ProposalKind.UNSUPPORTED:
            await reply(f"{NOT_UNDERSTOOD} {CONFIRM_HINT}" if waiting else NOT_UNDERSTOOD)
            return

        turn = plan_turn(draft.fields if draft is not None else {}, grounded, workspace)
        version = 1 if draft is None else draft.draft_version + int(turn.changed)
        summarize = turn.complete and (
            turn.changed or draft is None or not draft.awaits_confirmation
        )
        if summarize:
            summarized: int | None = version
        else:
            summarized = None if draft is None else draft.summarized_version
        try:
            await self.drafts.put(
                context,
                message.channel,
                fields=turn.fields,
                draft_version=version,
                summarized_version=summarized,
                expires_at=self.clock.now() + DRAFT_TTL,
                previous_version=None if draft is None else draft.draft_version,
            )
        except ProposalDraftChangedError:
            await reply(ALREADY_HANDLED)
            return
        await reply(turn.reply)

    # ---- "Đồng ý" -------------------------------------------------------------

    async def _confirm(
        self,
        message: ChatMessage,
        context: AccessContext,
        draft: ProposalDraft,
        workspace: str,
        reply: Reply,
    ) -> None:
        if not draft.awaits_confirmation:
            # Changed since its last summary (or never summarised): nothing is
            # created on a version the person has not seen.
            missing, _ = _check(draft.fields)
            if missing:
                await reply(f"Anh/chị gửi thêm {_labels(missing)} giúp mình.")
                return
            try:
                await self.drafts.put(
                    context,
                    message.channel,
                    fields=draft.fields,
                    draft_version=draft.draft_version,
                    summarized_version=draft.draft_version,
                    expires_at=self.clock.now() + DRAFT_TTL,
                    previous_version=draft.draft_version,
                )
            except ProposalDraftChangedError:
                await reply(ALREADY_HANDLED)
                return
            await reply(f"{RESUMMARIZED}\n{summary_text(draft.fields, workspace)}")
            return
        fields = draft.fields
        try:
            case = await self.propose.handle(
                context,
                proposal_code=fields[ProposalField.PROPOSAL_CODE],
                product_name=fields[ProposalField.PRODUCT_NAME],
                category=fields[ProposalField.CATEGORY],
                origin=ProposalOrigin(
                    channel=message.channel,
                    chat_ref=chat_reference(message.channel, message.chat_id),
                ),
                consume=draft.claim(),
            )
        except ProposalDraftChangedError:
            await reply(ALREADY_HANDLED)
            return
        except ConflictError:
            # The one other conflict `add` raises: the code is taken in this
            # tenant (in any workspace). Drop it and ask for another; the next
            # summary is owed before anything is created.
            code = fields[ProposalField.PROPOSAL_CODE]
            remaining: dict[ProposalField, str] = {
                k: v for k, v in fields.items() if k is not ProposalField.PROPOSAL_CODE
            }
            try:
                await self.drafts.put(
                    context,
                    message.channel,
                    fields=remaining,
                    draft_version=draft.draft_version + 1,
                    summarized_version=draft.summarized_version,
                    expires_at=self.clock.now() + DRAFT_TTL,
                    previous_version=draft.draft_version,
                )
            except ProposalDraftChangedError:
                await reply(ALREADY_HANDLED)
                return
            await reply(duplicate_code(code))
            return
        await reply(
            f"Đã tạo hồ sơ phát triển sản phẩm «{case.proposal_code}» — {case.product_name} "
            f"ở workspace «{workspace}»; anh/chị là PIC. Tải ảnh và làm tiếp ở "
            f"{self._case_url(case.id.value)}"
        )

    def _case_url(self, case_id: UUID) -> str:
        return f"{self.web_url.rstrip('/')}/supply-chain/product-cases/{case_id}"
