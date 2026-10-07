"""The portal half of a decision on Zalo: the view receipt and the code it issues.

ADR 0007 with its 2026-10-06 amendment (channels Z5). Opening an
approval on the portal records a receipt; asking for a code (with the comment
the decision will carry) records a fresh receipt and issues a code bound to it,
but only when every one of these holds — each refusal has its own reason, which
the page says in words:

- the approval is pending;
- the viewer may decide it: `approvals.decide` and its stamped scope, the very
  checks `decide` runs (`ApproveAndResumeService.may_decide`);
- the viewer did not ask for it (for every type: on the web this applies to
  strict prefixes only, ADR 0007 point 3);
- a context answers for the type's subject version (`ApprovalSubjectVersions`)
  and names one; otherwise the type is decided on the web only;
- the viewer has linked a chat to decide from;
- a strict type has a comment (amendment 2026-10-06: the comment is written on
  the portal, and the chat carries only the code);
- the deployment holds a code key at all.

The code is returned once, in the response, and stored only as its HMAC.
Generic platform code: an upstream candidate.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_kernel.errors import ConflictError, DomainError, NotFoundError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.approval_codes import (
    CODE_TTL,
    MAX_COMMENT_LENGTH,
    ApprovalCodeStorePort,
    ApprovalSubjectVersions,
    DecisionCodeKey,
    LinkedChatPort,
    NewDecisionCode,
    ViewReceipt,
    new_code,
)
from dw_platform.application.authorization import ApprovalAudience, ScopeAuthorizationService
from dw_platform.application.ports import PlatformUnitOfWork, PlatformUnitOfWorkFactory
from dw_platform.domain.approval import ApprovalRequest, ApprovalStatus

# How many fresh digits issuing tries before giving up on one that no other
# open code of the same person shares. Six digits and a handful of open codes
# make a second draw rare and a ninth one practically impossible.
_DRAWS = 8


class CodeUnavailable(StrEnum):
    """Why the portal offers no code. Each one is a sentence on the page."""

    NOT_PENDING = "not_pending"
    CANNOT_DECIDE = "cannot_decide"
    REQUESTER = "requester"
    WEB_ONLY = "web_only"
    NOT_LINKED = "not_linked"
    COMMENT_REQUIRED = "comment_required"
    CHANNEL_OFF = "channel_off"


@dataclass(frozen=True, slots=True)
class IssuedCode:
    code: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ViewOutcome:
    viewed_at: datetime
    requires_comment: bool
    issued: IssuedCode | None
    unavailable: CodeUnavailable | None


def approve_command(code: str) -> str:
    return f"DUYỆT {code}"


def reject_command(code: str) -> str:
    return f"KHÔNG {code} <lý do>"


@dataclass
class ApprovalViewService:
    uow_factory: PlatformUnitOfWorkFactory
    approval_flow: ApproveAndResumeService
    store: ApprovalCodeStorePort
    subjects: ApprovalSubjectVersions
    chats: LinkedChatPort
    # None: this deployment holds no code key, and no code is ever issued.
    key: DecisionCodeKey | None
    clock: UtcClock
    ids: IdGenerator

    async def view(
        self,
        *,
        approval_id: uuid.UUID,
        context: AccessContext,
        authorization: ScopeAuthorizationService,
        comment: str = "",
        issue_code: bool = False,
    ) -> ViewOutcome:
        if len(comment) > MAX_COMMENT_LENGTH:
            raise DomainError("the comment is too long", details={"max_length": MAX_COMMENT_LENGTH})
        request = await self._visible(approval_id, context, authorization)
        port = self.subjects.for_type(request.approval_type)
        subject_version = None if port is None else await port.version_of(context, request)
        receipt = ViewReceipt(
            id=self.ids.new_uuid(),
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            approval_id=request.id,
            user_id=context.principal_id,
            approval_version=request.version,
            subject_version=subject_version,
        )
        requires_comment = self.approval_flow.is_strict(request.approval_type)
        unavailable = await self._unavailable(
            request,
            context,
            authorization,
            subject_version=subject_version,
            missing_comment=issue_code and requires_comment and not comment.strip(),
        )
        issued: IssuedCode | None = None
        new: NewDecisionCode | None = None
        if issue_code and unavailable is None and self.key is not None:
            code = await self._fresh_code(self.key, context.principal_id)
            expires_at = self.clock.now().astimezone(UTC) + CODE_TTL
            new = NewDecisionCode(
                id=self.ids.new_uuid(),
                code_hash=self.key.digest(request.id, context.principal_id, code),
                comment=comment.strip(),
                expires_at=expires_at,
            )
            issued = IssuedCode(code=code, expires_at=expires_at)
        # Two issues for the same approval at once: the one-open-code index
        # refuses the second (`ConflictError`), and the page asks again.
        await self.store.record_view(context, receipt, new)
        return ViewOutcome(
            viewed_at=self.clock.now().astimezone(UTC),
            requires_comment=requires_comment,
            issued=issued,
            unavailable=unavailable,
        )

    async def _visible(
        self,
        approval_id: uuid.UUID,
        context: AccessContext,
        authorization: ScopeAuthorizationService,
    ) -> ApprovalRequest:
        await authorization.require(
            context=context,
            action="approvals.read",
            resource_type="approval_request",
            resource_id=str(approval_id),
        )
        uow: PlatformUnitOfWork
        async with self.uow_factory(context) as uow:
            request = await uow.approvals.get(
                approval_id,
                workspace_id=context.workspace_id,
                audience=ApprovalAudience.of(context, authorization),
            )
        if request is None:
            raise NotFoundError("approval request not found")
        return request

    async def _unavailable(
        self,
        request: ApprovalRequest,
        context: AccessContext,
        authorization: ScopeAuthorizationService,
        *,
        subject_version: str | None,
        missing_comment: bool,
    ) -> CodeUnavailable | None:
        if request.status is not ApprovalStatus.PENDING:
            return CodeUnavailable.NOT_PENDING
        if not self.approval_flow.may_decide(request, context, authorization):
            return CodeUnavailable.CANNOT_DECIDE
        if request.requested_by.value == context.principal_id:
            return CodeUnavailable.REQUESTER
        if subject_version is None:
            return CodeUnavailable.WEB_ONLY
        if self.key is None:
            return CodeUnavailable.CHANNEL_OFF
        if await self.chats.zalo_id_for(context.principal_id) is None:
            return CodeUnavailable.NOT_LINKED
        if missing_comment:
            return CodeUnavailable.COMMENT_REQUIRED
        return None

    async def _fresh_code(self, key: DecisionCodeKey, user_id: uuid.UUID) -> str:
        """Digits no other open code of this person shares, so the code alone
        names one approval when the chat sends it back."""
        taken = await self.store.open_codes(user_id)
        for _ in range(_DRAWS):
            code = new_code()
            if not any(key.matches(o.code_hash, o.approval_id, user_id, code) for o in taken):
                return code
        raise ConflictError("could not draw a free code; ask again")


class AdmissionRefusedError(ConflictError):
    """A code did not admit the decision; nothing was written."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            "the decision code does not admit this decision", details={"reason": reason}
        )
        self.reason = reason


ADMISSION_STALE = "approval_changed"
ADMISSION_SPENT = "code_spent"


@dataclass(frozen=True)
class CodeAdmission:
    """A single-use code admitting one decision (`DecisionAdmission`).

    Inside `decide`'s unit of work: the approval read there must still be the
    version the code's view saw, and the code is consumed by one conditional
    UPDATE; either failing refuses, and the rollback leaves the code unused.
    """

    code_id: uuid.UUID
    user_id: uuid.UUID
    receipt_id: uuid.UUID
    approval_version: int
    subject_version: str
    message_id: str
    chat_reference: str

    async def admit(
        self, uow: PlatformUnitOfWork, request: ApprovalRequest, context: AccessContext
    ) -> Mapping[str, object]:
        if request.version != self.approval_version:
            raise AdmissionRefusedError(ADMISSION_STALE)
        if not await uow.decision_codes.consume(self.code_id, self.user_id):
            raise AdmissionRefusedError(ADMISSION_SPENT)
        return {
            "approval_version": self.approval_version,
            "subject_version": self.subject_version,
            "receipt_id": str(self.receipt_id),
            "code_id": str(self.code_id),
            "message_id": self.message_id,
            "chat_reference": self.chat_reference,
        }
