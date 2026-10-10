"""Single-use decision codes: a decision in a chat after a view on the portal.

ADR 0007 with its 2026-10-06 amendment (channels Z5). A person opens
an approval on the portal; the page records a **view receipt** (the approval's
version and its subject's version at that moment) and, when asked and allowed,
issues a **code** bound to that receipt and to the comment typed there. The chat
then carries only `DUYỆT <mã>` or `KHÔNG <mã> <lý do>`. What this module owns:

- the code's shape and lifetime (`CODE_DIGITS`, `CODE_TTL`, `MAX_WRONG_TRIES`)
  and its keyed hash (`DecisionCodeKey`): HMAC-SHA256 under a server key, over
  approval id, user id and digits. The digits are never stored, logged or sent;
- the seam a context answers "which version of the thing being approved is
  this?" through (`ApprovalSubjectVersionPort`), registered by approval-type
  prefix at the composition root (`ApprovalSubjectVersions`). A type nobody
  answers for gets no code: it is decided on the web only (fail closed);
- the ports the services in `dw_agent_runtime.approval_codes` and
  `dw_agent_runtime.channel_decisions` persist through.

Generic platform code, with no product name in it: an upstream candidate.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from dw_platform.application.access_context import AccessContext
from dw_platform.domain.approval import ApprovalRequest
from dw_platform.domain.audit import AuditEvent

CODE_DIGITS = 6
CODE_TTL = timedelta(minutes=10)
# The fifth wrong try against a person's open codes locks each of them.
MAX_WRONG_TRIES = 5
# The longest comment a code carries, the column's CHECK.
MAX_COMMENT_LENGTH = 2000


def new_code() -> str:
    """Six digits from the OS's CSPRNG, leading zeros kept."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


@dataclass(frozen=True)
class DecisionCodeKey:
    """The server key codes are hashed under (`DW_APPROVAL_CODE_SECRET`).

    Keyed because a plain hash of six digits is reversed by a million tries:
    whoever reads the table must still not learn a code. The approval and the
    person are inside the MAC, so the same digits issued to two people or for
    two approvals never share a hash, and a code is only ever compared with the
    codes of the person whose chat sent it.
    """

    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.secret) < 16:
            raise ValueError("the approval code secret must be at least 16 bytes")

    def digest(self, approval_id: uuid.UUID, user_id: uuid.UUID, code: str) -> bytes:
        message = f"{approval_id}:{user_id}:{code}".encode()
        return hmac.new(self.secret, message, hashlib.sha256).digest()

    def matches(self, stored: bytes, approval_id: uuid.UUID, user_id: uuid.UUID, code: str) -> bool:
        return hmac.compare_digest(stored, self.digest(approval_id, user_id, code))


# ---- the subject's version ------------------------------------------------


class ApprovalSubjectVersionPort(Protocol):
    """The current version of what an approval of some type decides on.

    Satisfied by the context that owns the subject (a product case, say), read
    under the caller's own context. None when the subject cannot be found: no
    code is issued and no decision is taken against a version nobody can name.
    """

    async def version_of(self, context: AccessContext, request: ApprovalRequest) -> str | None: ...


@dataclass
class ApprovalSubjectVersions:
    """Which port answers for which approval types, by prefix.

    Filled at each composition root, the way `strict_approval_prefixes` is; a
    context adds an entry, never a branch here. The longest matching prefix
    wins, so a narrower registration is never shadowed by a wider one.
    """

    _by_prefix: dict[str, ApprovalSubjectVersionPort] = field(default_factory=dict)

    def register(self, prefix: str, port: ApprovalSubjectVersionPort) -> None:
        if not prefix.strip():
            raise ValueError("a subject version port needs a type prefix")
        if prefix in self._by_prefix:
            raise ValueError(f"a subject version port is already registered for {prefix!r}")
        self._by_prefix[prefix] = port

    def for_type(self, approval_type: str) -> ApprovalSubjectVersionPort | None:
        found = [p for p in self._by_prefix if approval_type.startswith(p)]
        return self._by_prefix[max(found, key=len)] if found else None


# ---- what is stored -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ViewReceipt:
    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    approval_id: uuid.UUID
    user_id: uuid.UUID
    approval_version: int
    subject_version: str | None


@dataclass(frozen=True, slots=True)
class NewDecisionCode:
    id: uuid.UUID
    code_hash: bytes = field(repr=False)
    comment: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class OpenCode:
    """One of a person's open codes, as much as issuing a new one needs:
    which approval it is for and its hash, to keep two open codes of the same
    person from sharing digits."""

    approval_id: uuid.UUID
    code_hash: bytes = field(repr=False)


class CodeState(StrEnum):
    OPEN = "open"
    USED = "used"
    EXPIRED = "expired"
    LOCKED = "locked"
    REISSUED = "reissued"


@dataclass(frozen=True, slots=True)
class StoredCode:
    """A person's code as the bot reads it, every state, with the database's
    own clock deciding `expired`."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    approval_id: uuid.UUID
    user_id: uuid.UUID
    receipt_id: uuid.UUID
    code_hash: bytes = field(repr=False)
    comment: str
    state: CodeState


@dataclass(frozen=True, slots=True)
class LastDecision:
    decided_at: datetime
    channel: str


@dataclass(frozen=True, slots=True)
class CodedApproval:
    """What a code points at, read under the code's own tenant and workspace."""

    request: ApprovalRequest
    receipt_approval_version: int
    receipt_subject_version: str | None
    last_decision: LastDecision | None


class ApprovalCodeStorePort(Protocol):
    """Receipts and codes, outside a decision's own transaction.

    Every method that reads across tenants is keyed by a user id the caller
    resolved server-side (the session's principal, or the chat's link row) and
    binds it as `app.principal_id`; every write binds the tenant and workspace
    of the row it writes.
    """

    async def record_view(
        self, context: AccessContext, receipt: ViewReceipt, code: NewDecisionCode | None
    ) -> None:
        """Insert the receipt; with a code, revoke the person's open code for
        this approval (`reissued`) and insert the new one, all in one
        transaction."""
        ...

    async def open_codes(self, user_id: uuid.UUID) -> Sequence[OpenCode]: ...

    async def codes_of(self, user_id: uuid.UUID) -> Sequence[StoredCode]:
        """Every code of the person still kept (the last day), newest first."""
        ...

    async def record_wrong_try(self, user_id: uuid.UUID) -> int:
        """Count one wrong try against each of the person's open codes, each
        under its own tenant and workspace; a code reaching `MAX_WRONG_TRIES` is
        revoked as `locked`. Returns how many codes were locked."""
        ...

    async def coded_approval(self, code: StoredCode) -> CodedApproval | None: ...

    async def record_refusal(self, event: AuditEvent) -> None: ...


class DecisionCodeLedgerPort(Protocol):
    """Consuming a code inside the decision's own unit of work."""

    async def consume(self, code_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """One conditional UPDATE: True when this call used the code; False
        when it was already used, revoked, expired, or someone else's."""
        ...


class LinkedChatPort(Protocol):
    """Whether a person has a chat a decision could come from."""

    async def zalo_id_for(self, user_id: uuid.UUID) -> str | None: ...
