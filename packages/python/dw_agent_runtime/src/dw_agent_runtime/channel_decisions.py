"""The chat half of a decision on Zalo: `DUYỆT <mã>` and `KHÔNG <mã> <lý do>`.

ADR 0007 with its 2026-10-06 amendment (channels Z5). A fixed
grammar read by code, never by a model: no tool decides, no intent schema has a
"decision" field. `ChannelDecisionCommand` is the first command a deployment
registers in its `ChannelCommandRegistry` (`dw_connectors.inbound`), so a reply
to a pending decision is never read as anything else; it satisfies that
registry's `ChannelCommand` structurally, so this package imports nothing of the
connectors.

`ChannelApprovalDecisionService` accepts a decision only when every check holds,
in this order, and otherwise refuses with one sentence per reason and an audit
event (`approval.channel_decision_refused`) under the code's tenant:

1. The digits match one of the sender's own codes (read across their tenants by
   `app.principal_id`, the chat's linked person, compared with
   `hmac.compare_digest`). No match is the SAME sentence for every case — a
   typo, a guess, another person's code, another tenant's — and counts one
   wrong try against each of the sender's open codes; the fifth locks them.
   The tenant and workspace come from the matched code's row, never the chat.
2. The code is open: not used, not replaced by a newer view, not locked, not
   expired (the database's clock).
3. The approval is pending; the sender did not ask for it (every type).
4. The approval's version and its subject's version are the ones the view saw.
5. The sender's context, built for the code's workspace from their CURRENT
   membership and cut to `approvals.decide` plus the approval's stamped scope,
   no role (`LinkedUserAccess.access_for`, ADR 0005 condition 2).
6. `ApproveAndResumeService.decide(channel="zalo", admission=CodeAdmission)`:
   the same scope, stamp, strict-prefix and workspace checks as the web, and
   the code consumed by one conditional UPDATE in the decision's transaction.

Generic platform code: an upstream candidate.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, timedelta, timezone
from enum import StrEnum
from typing import Protocol

from dw_agent_runtime.approval_codes import (
    ADMISSION_STALE,
    AdmissionRefusedError,
    CodeAdmission,
)
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_kernel.channels import chat_reference
from dw_kernel.errors import ConflictError, NotFoundError, PermissionDeniedError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    CODE_DIGITS,
    MAX_WRONG_TRIES,
    ApprovalCodeStorePort,
    ApprovalSubjectVersions,
    CodedApproval,
    CodeState,
    DecisionCodeKey,
    StoredCode,
)
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.approval import APPROVALS_DECIDE, ApprovalStatus
from dw_platform.domain.audit import AuditEvent

logger = logging.getLogger("dw_agent_runtime.channel_decisions")

Reply = Callable[[str], Awaitable[None]]

REFUSED_ACTION = "approval.channel_decision_refused"

_APPROVE = "DUYET"
_REJECT = "KHONG"
_VN = timezone(timedelta(hours=7))

APPROVE_HINT = (
    f"Để duyệt, gõ đúng: DUYỆT <mã {CODE_DIGITS} số>. Mã hiện trên trang yêu cầu ở cổng, "
    "sau khi đăng nhập; nhận xét cũng nhập ở đó. Để không duyệt: KHÔNG <mã> <lý do>."
)
REJECT_HINT = f"Để không duyệt, gõ đúng: KHÔNG <mã {CODE_DIGITS} số> <lý do>. Lý do là bắt buộc."


class Refusal(StrEnum):
    WRONG_CODE = "wrong_code"
    EXPIRED = "code_expired"
    USED = "code_used"
    REISSUED = "code_reissued"
    LOCKED = "code_locked"
    STALE = "changed_since_view"
    NO_PERMISSION = "no_permission"
    REQUESTER = "requester"
    ALREADY_DECIDED = "already_decided"
    COMMENT_REQUIRED = "comment_required"
    NOT_DECIDED = "not_decided"
    DISABLED = "channel_off"


_REPLIES: dict[Refusal, str] = {
    # One sentence for a typo, a guess and someone else's code alike: the reply
    # must not tell a sender that the digits belong to anybody.
    Refusal.WRONG_CODE: "Mã không đúng hoặc đã hết hạn.",
    Refusal.EXPIRED: "Mã đã hết hạn. Mở lại yêu cầu trên cổng để lấy mã mới.",
    Refusal.USED: "Mã này đã được dùng; mỗi mã chỉ quyết được một lần.",
    Refusal.REISSUED: (
        "Mã này đã được thay bằng mã mới khi anh/chị mở lại yêu cầu. Dùng mã đang hiện trên cổng."
    ),
    Refusal.LOCKED: (
        f"Mã này đã bị khóa vì nhập sai {MAX_WRONG_TRIES} lần. "
        "Mở lại yêu cầu trên cổng để lấy mã mới."
    ),
    Refusal.STALE: (
        "Yêu cầu hoặc hồ sơ đã thay đổi sau khi anh/chị xem. "
        "Mở lại liên kết để xem bản mới và lấy mã mới."
    ),
    Refusal.NO_PERMISSION: "Anh/chị không có quyền quyết yêu cầu này.",
    Refusal.REQUESTER: "Anh/chị là người tạo yêu cầu này nên không tự quyết được.",
    Refusal.COMMENT_REQUIRED: (
        "Loại yêu cầu này cần nhận xét. Nhập nhận xét trên cổng rồi lấy mã mới."
    ),
    Refusal.NOT_DECIDED: "Chưa quyết được qua Zalo. Mở lại yêu cầu trên cổng để quyết.",
    Refusal.DISABLED: "Quyết qua Zalo chưa bật ở hệ thống này; anh/chị quyết trên cổng.",
}

_CHANNEL_LABEL = {"web": "cổng web", "zalo": "Zalo"}


def already_decided_reply(coded: CodedApproval) -> str:
    request = coded.request
    if request.status is ApprovalStatus.CANCELLED:
        return "Yêu cầu này đã được hủy."
    last = coded.last_decision
    if last is None:
        return "Yêu cầu này đã được quyết."
    when = last.decided_at.astimezone(_VN).strftime("%H:%M ngày %d/%m/%Y")
    return (
        f"Yêu cầu này đã được quyết lúc {when} (giờ Việt Nam), "
        f"trên {_CHANNEL_LABEL.get(last.channel, last.channel)}."
    )


def decided_reply(approve: bool) -> str:
    verdict = "DUYỆT" if approve else "KHÔNG DUYỆT"
    return f"Đã ghi quyết định {verdict}. Kết quả xem trên cổng."


# ---- the grammar ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedDecision:
    approve: bool
    code: str
    # The rejection's reason as typed; empty for an approval.
    reason: str


@dataclass(frozen=True, slots=True)
class Malformed:
    """A message that is plainly an attempt at a decision but not in the
    grammar. It gets the grammar back, and nothing else reads it."""

    hint: str


def _fold(word: str) -> str:
    """Case and accents dropped: "Duyệt", "DUYET", "duyêt" are one word."""
    decomposed = unicodedata.normalize("NFD", word.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).upper()


def _is_code(token: str) -> bool:
    return len(token) == CODE_DIGITS and token.isascii() and token.isdigit()


def parse_decision(text: str) -> ParsedDecision | Malformed | None:
    """`DUYỆT <mã>` or `KHÔNG <mã> <lý do>`, after NFC; None when the message
    is not a decision at all, so the next command reads it.

    "duyệt ..." in any other shape is Malformed: a message that starts with
    the verb is never handed on to a model. "không ..." is only this
    command's when a number follows, since "không" opens ordinary sentences.
    """
    tokens = unicodedata.normalize("NFC", text).strip().split(maxsplit=2)
    if not tokens:
        return None
    head = _fold(tokens[0])
    if head == _APPROVE:
        if len(tokens) == 2 and _is_code(tokens[1]):
            return ParsedDecision(approve=True, code=tokens[1], reason="")
        return Malformed(APPROVE_HINT)
    if head == _REJECT and len(tokens) >= 2 and tokens[1].isascii() and tokens[1].isdigit():
        reason = tokens[2].strip() if len(tokens) == 3 else ""
        if _is_code(tokens[1]) and reason:
            return ParsedDecision(approve=False, code=tokens[1], reason=reason)
        return Malformed(REJECT_HINT)
    return None


# ---- the service ----------------------------------------------------------


class LinkedDecisionAccessPort(Protocol):
    """`LinkedUserAccess.access_for`: the person's own membership in one
    workspace, cut to a ceiling, no role; None when they may not act there."""

    async def access_for(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        ceiling: frozenset[str],
    ) -> AccessContext | None: ...


@dataclass(frozen=True, slots=True)
class ChannelDecisionOutcome:
    decided: bool
    reason: Refusal | None
    reply: str


@dataclass(frozen=True, slots=True)
class _Message:
    channel: str
    message_id: str
    chat_id: str


@dataclass
class ChannelApprovalDecisionService:
    approval_flow: ApproveAndResumeService
    authorization: ScopeAuthorizationService
    store: ApprovalCodeStorePort
    subjects: ApprovalSubjectVersions
    access: LinkedDecisionAccessPort
    # None: no code key in this deployment, and no decision is taken by chat.
    key: DecisionCodeKey | None
    clock: UtcClock
    ids: IdGenerator

    async def decide(
        self,
        *,
        user_id: uuid.UUID,
        channel: str,
        message_id: str,
        chat_id: str,
        decision: ParsedDecision,
    ) -> ChannelDecisionOutcome:
        if self.key is None:
            return _refused(Refusal.DISABLED)
        message = _Message(channel=channel, message_id=message_id, chat_id=chat_id)
        codes = await self.store.codes_of(user_id)
        key = self.key
        matched = [
            c for c in codes if key.matches(c.code_hash, c.approval_id, user_id, decision.code)
        ]
        if not matched:
            locked = await self.store.record_wrong_try(user_id)
            # No tenant to audit under: the digits named none of this person's
            # codes. Logged without them.
            logger.info("channel decision: no code matched (locked %d)", locked)
            return _refused(Refusal.WRONG_CODE)
        opened = [c for c in matched if c.state is CodeState.OPEN]
        if len(opened) > 1:
            # Two open codes sharing digits (issuing draws around this): the
            # digits do not name one approval, so neither is decided.
            return await self._refuse(Refusal.STALE, opened[0], message)
        code = opened[0] if opened else matched[0]
        state_refusal = {
            CodeState.USED: Refusal.USED,
            CodeState.EXPIRED: Refusal.EXPIRED,
            CodeState.LOCKED: Refusal.LOCKED,
            CodeState.REISSUED: Refusal.REISSUED,
        }.get(code.state)
        if state_refusal is not None:
            return await self._refuse(state_refusal, code, message)

        coded = await self.store.coded_approval(code)
        if coded is None:
            return await self._refuse(Refusal.WRONG_CODE, code, message)
        request = coded.request
        if request.status is not ApprovalStatus.PENDING:
            return await self._refuse(
                Refusal.ALREADY_DECIDED, code, message, reply=already_decided_reply(coded)
            )
        if request.requested_by.value == user_id:
            return await self._refuse(Refusal.REQUESTER, code, message)
        if coded.receipt_approval_version != request.version:
            return await self._refuse(Refusal.STALE, code, message)

        ceiling = frozenset({APPROVALS_DECIDE}) | (
            frozenset({request.required_scope}) if request.required_scope else frozenset()
        )
        context = await self.access.access_for(user_id, code.tenant_id, code.workspace_id, ceiling)
        if context is None:
            return await self._refuse(Refusal.NO_PERMISSION, code, message)
        port = self.subjects.for_type(request.approval_type)
        current = None if port is None else await port.version_of(context, request)
        if current is None or current != coded.receipt_subject_version:
            return await self._refuse(Refusal.STALE, code, message)

        comment = code.comment if decision.approve else _joined(code.comment, decision.reason)
        if self.approval_flow.is_strict(request.approval_type) and not comment.strip():
            return await self._refuse(Refusal.COMMENT_REQUIRED, code, message)

        try:
            await self.approval_flow.decide(
                approval_id=request.id,
                approve=decision.approve,
                comment=comment,
                context=context,
                authorization=self.authorization,
                channel=channel,
                admission=CodeAdmission(
                    code_id=code.id,
                    user_id=user_id,
                    receipt_id=code.receipt_id,
                    approval_version=coded.receipt_approval_version,
                    subject_version=current,
                    message_id=message_id,
                    chat_reference=chat_reference(channel, chat_id),
                ),
            )
        except AdmissionRefusedError as exc:
            reason = Refusal.STALE if exc.reason == ADMISSION_STALE else Refusal.USED
            return await self._refuse(reason, code, message)
        except (PermissionDeniedError, NotFoundError):
            # What the web answers the same person: refused, or not theirs to see.
            return await self._refuse(Refusal.NO_PERMISSION, code, message)
        except ConflictError:
            again = await self.store.coded_approval(code)
            if again is not None and again.request.status is not ApprovalStatus.PENDING:
                return await self._refuse(
                    Refusal.ALREADY_DECIDED, code, message, reply=already_decided_reply(again)
                )
            return await self._refuse(Refusal.NOT_DECIDED, code, message)
        return ChannelDecisionOutcome(
            decided=True, reason=None, reply=decided_reply(decision.approve)
        )

    async def _refuse(
        self,
        reason: Refusal,
        code: StoredCode,
        message: _Message,
        *,
        reply: str | None = None,
    ) -> ChannelDecisionOutcome:
        """One audit event per refusal, under the code's tenant and workspace:
        the reason, the code's id, the message — never the digits typed."""
        await self.store.record_refusal(
            AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(code.tenant_id),
                workspace_id=WorkspaceId(code.workspace_id),
                actor_id=UserId(code.user_id),
                action=REFUSED_ACTION,
                resource_type="approval_request",
                resource_id=str(code.approval_id),
                occurred_at=self.clock.now().astimezone(UTC),
                details={
                    "reason": reason.value,
                    "channel": message.channel,
                    "code_id": str(code.id),
                    "receipt_id": str(code.receipt_id),
                    "message_id": message.message_id,
                    "chat_reference": chat_reference(message.channel, message.chat_id),
                },
            )
        )
        return _refused(reason, reply)


def _refused(reason: Refusal, reply: str | None = None) -> ChannelDecisionOutcome:
    return ChannelDecisionOutcome(decided=False, reason=reason, reply=reply or _REPLIES[reason])


def _joined(comment: str, reason: str) -> str:
    """A rejection records the portal's comment, then the reason the chat gave."""
    return f"{comment}\n{reason}" if comment.strip() else reason


# ---- the chat command -----------------------------------------------------


class ChatMessage(Protocol):
    """What this command reads of `dw_connectors.inbound.InboundMessage`."""

    @property
    def channel(self) -> str: ...

    @property
    def message_id(self) -> str: ...

    @property
    def chat_id(self) -> str: ...

    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class ChannelDecisionCommand:
    """`DUYỆT`/`KHÔNG` from a linked chat. Registered first.

    `ceiling` is what the router cuts the context it hands over to; this
    command uses that context for one thing, the person's id. The context it
    decides with is built for the CODE's workspace, cut to `approvals.decide`
    plus that approval's stamp (step 5 above).
    """

    service: ChannelApprovalDecisionService

    @property
    def ceiling(self) -> frozenset[str]:
        return frozenset({APPROVALS_DECIDE})

    async def handle(self, message: ChatMessage, context: AccessContext, reply: Reply) -> bool:
        parsed = parse_decision(message.text)
        if parsed is None:
            return False
        if isinstance(parsed, Malformed):
            await reply(parsed.hint)
            return True
        outcome = await self.service.decide(
            user_id=context.principal_id,
            channel=message.channel,
            message_id=message.message_id,
            chat_id=message.chat_id,
            decision=parsed,
        )
        await reply(outcome.reply)
        return True
