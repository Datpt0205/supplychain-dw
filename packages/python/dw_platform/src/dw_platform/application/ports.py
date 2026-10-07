"""Platform application ports.

Adapters (Keycloak OIDC verifier, SQL membership store, policy engine) implement
these; handlers depend only on the protocols.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from dw_kernel.pagination import Page, PageRequest
from dw_platform.application.access_context import AccessContext

if TYPE_CHECKING:
    from dw_platform.application.approval_codes import DecisionCodeLedgerPort
    from dw_platform.application.authorization import ApprovalAudience
    from dw_platform.application.directory import IdentityRef, WorkspaceMember
    from dw_platform.domain.approval import ApprovalDecision, ApprovalRequest
    from dw_platform.domain.audit import AuditEvent
    from dw_platform.domain.feedback import Feedback, FeedbackAttachment
    from dw_platform.domain.outbox import OutboxBacklog, OutboxEvent


class VerifiedIdentity(Protocol):
    """Claims extracted from a cryptographically verified token."""

    @property
    def subject(self) -> str: ...

    @property
    def email(self) -> str | None: ...

    @property
    def issuer(self) -> str: ...


class TokenVerifierPort(Protocol):
    """Verifies a bearer token and returns trusted claims.

    Implementations: Keycloak OIDC (production/compose), local dev issuer
    (tests/dev). Raises ``PermissionDeniedError`` on any verification failure.
    """

    async def verify(self, token: str) -> VerifiedIdentity: ...


class AccessContextFactoryPort(Protocol):
    """Builds an :class:`AccessContext` from verified identity + membership.

    Never trusts a client-supplied tenant id without checking membership.
    """

    async def build(
        self,
        identity: VerifiedIdentity,
        requested_tenant_id: UUID | None,
        requested_workspace_id: UUID | None,
    ) -> AccessContext: ...


class AuthorizationPort(Protocol):
    """Decides whether a principal may perform an action on a resource.

    Distinct from entitlement (plan capability) checks by design (§15.1).
    """

    async def require(
        self,
        *,
        context: AccessContext,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
    ) -> None:
        """Raise ``PermissionDeniedError`` when the action is not allowed."""
        ...


class EntitlementPort(Protocol):
    """Checks plan-level capability/quota; raises ``EntitlementDeniedError``."""

    async def require_feature(self, context: AccessContext, feature: str) -> None: ...


class WorkspaceDirectoryPort(Protocol):
    """Lists the people in the caller's workspace, for owner and assignee UI.

    Read-only on purpose: membership is granted by the identity plane, so a
    directory that could write would be a second, competing way to join a
    workspace.
    """

    async def list_members(self, context: AccessContext) -> list[WorkspaceMember]: ...

    async def list_candidates(self, context: AccessContext) -> list[IdentityRef]:
        """Signed-in identities not already in this workspace — a grant picker."""
        ...


class PolicyOverridePort(Protocol):
    """A tenant's own replacement for a platform-default policy document.

    The write half `TenantOverlay`'s own docstring already names as a
    covered artifact kind ("a policy") but never had storage built for.
    `content` is raw JSON: this port has no opinion on what shape a given
    `policy_id` needs — validating a submitted document against its own
    schema is the owning bounded context's job (the Pydantic model it
    declares for its own `policy_id`), the same
    split every other port in this platform draws between "persist this"
    and "is this valid". A tenant's override is the WHOLE document, never
    merged field-by-field with the platform default — matches how every
    other `TenantOverlay`-resolved artifact already resolves.
    """

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None: ...

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        """Persists `content` and `audit` in the SAME transaction — a policy
        override changes what a business rule (an SLA breach, eventually a
        retention term) means for a whole tenant, and CLAUDE.md's own
        provenance rule applies to it exactly as it does to a membership
        grant: the write and its audit trail commit together or not at all.
        Required, not optional — `membership_admin.py`'s `grant()`/`revoke()`
        already take `audit: AuditEvent` the same way, not a keyword a caller
        can forget to pass."""
        ...


class ApprovalRepositoryPort(Protocol):
    """Persistence for the approval aggregate (tenant-scoped via RLS).

    Reads are narrowed to the caller's workspace by the repository, since RLS
    on this table narrows by tenant only: another workspace's request is
    absent, exactly like another tenant's. And to what the caller's
    `ApprovalAudience` may see (ADR 0004): a stamped request the caller may
    neither decide nor asked for is absent the same way."""

    async def add(self, request: ApprovalRequest) -> None: ...

    async def get(
        self, request_id: UUID, *, workspace_id: UUID, audience: ApprovalAudience
    ) -> ApprovalRequest | None: ...

    async def save(self, request: ApprovalRequest) -> None:
        """Persist state transition with optimistic concurrency on version."""
        ...

    async def add_decision(self, decision: ApprovalDecision) -> None: ...

    async def list_pending(
        self, request: PageRequest, *, workspace_id: UUID, audience: ApprovalAudience
    ) -> Page[ApprovalRequest]:
        """The inbox, newest first and resumable. Pending work is bounded by how
        fast humans clear it, which on a stalled tenant is not bounded at all."""
        ...


class AuditRepositoryPort(Protocol):
    """Append-only audit trail; no update/delete exists by design.

    Reads are narrowed to the caller's workspace by the repository, since RLS
    on this table narrows by tenant only."""

    async def append(self, event: AuditEvent) -> None: ...

    async def list_page(self, request: PageRequest, *, workspace_id: UUID) -> Page[AuditEvent]:
        """Newest first, resumable. The audit trail is the table that grows
        without bound, so it is the one a bare ``limit`` truncates soonest."""
        ...

    async def list_for_run(
        self, run_id: UUID, *, workspace_id: UUID, limit: int = 100
    ) -> list[AuditEvent]:
        """One run's events, oldest first. Not paged: this is the timeline of a
        single aggregate, bounded by that run's own length rather than by how
        long the tenant has been a customer."""
        ...


class FeedbackRepositoryPort(Protocol):
    """Member feedback: everyone in a workspace may add; admins read the inbox."""

    async def add(
        self,
        *,
        feedback_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        author_id: UUID,
        category: str,
        message: str,
        module: str | None = None,
        page_path: str | None = None,
        suggestion: str | None = None,
    ) -> None: ...

    async def add_attachment(
        self,
        *,
        attachment_id: UUID,
        feedback_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        object_key: str,
        content_type: str,
        size_bytes: int,
    ) -> None: ...

    async def list_page(self, request: PageRequest) -> Page[Feedback]:
        """Newest first and resumable, each with its attachments."""
        ...

    async def get_attachment(
        self, feedback_id: UUID, attachment_id: UUID
    ) -> FeedbackAttachment | None: ...


class FeedbackAttachmentStoragePort(Protocol):
    """Raw bytes behind a feedback screenshot (spec 003 US5).

    ``put`` completes before the rows commit; a ``put`` that fails aborts the
    whole feedback, and what was already stored is deleted best-effort. The
    composition root satisfies this with the same object-storage adapter the
    CRM's documents use, on a bucket of its own - this package may not import
    the storage SDK (import-linter), only name what it needs.
    """

    async def put(self, key: str, data: bytes, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def list(self, prefix: str) -> list[str]:
        """Every key under `prefix`. Tenant offboarding's export/purge is the
        only caller that needs the whole set rather than one key it already
        knows."""
        ...


class OutboxRepositoryPort(Protocol):
    """Transactional outbox written in the same transaction as aggregates."""

    async def add(self, event: OutboxEvent) -> None: ...

    async def list_unprocessed(self, limit: int = 100) -> list[OutboxEvent]: ...

    async def has_unprocessed(self, event_type: str, aggregate_id: uuid.UUID) -> bool:
        """Còn lượt nào của event này trên bản ghi này chưa xử lý xong không?

        Hỏi hẹp thay vì kéo cả lô chưa xử lý về rồi lọc: màn hình chỉ cần biết
        "có đang chạy không", và một tenant bận có thể có hàng trăm dòng chờ
        mà không dòng nào thuộc bản ghi đang mở.
        """
        ...


class OutboxDrainPort(Protocol):
    """The worker's side of the outbox: no tenant, one batch at a time.

    Kept apart from ``OutboxRepositoryPort`` because that one lives inside a
    tenant-scoped unit of work and this one deliberately does not: a dispatcher
    serves every tenant on one connection, which is a different authority and
    wants to be visible as one.
    """

    async def claim_batch(
        self, *, event_types: Sequence[str], limit: int, max_attempts: int
    ) -> list[OutboxEvent]:
        """Take the oldest undelivered events of the named types, across tenants.

        Only the types the caller can actually handle: an event nobody consumes
        yet must stay undelivered rather than be drained into nothing. The claim
        is ``FOR UPDATE SKIP LOCKED`` plus an attempt increment in one
        transaction, so two dispatchers take different rows and a process that
        dies mid-delivery leaves the attempt counted.
        """
        ...

    async def mark_processed(self, event_id: UUID) -> None:
        """Record delivery. Called after the effect, so a crash retries it."""
        ...

    async def record_failure(self, event_id: UUID, *, error: str) -> None:
        """Keep the reason a delivery failed; the attempt was already counted."""
        ...

    async def backlog(self, *, event_types: Sequence[str], max_attempts: int) -> OutboxBacklog:
        """Count and age of what `claim_batch` would still find, for the same
        filter — so the number a dashboard shows matches what the dispatcher
        actually owes, not a guess at it."""
        ...


class PlatformUnitOfWork(Protocol):
    """One transaction with trusted tenant context applied via SET LOCAL.

    Entering the UoW binds the RLS context from the AccessContext it was
    created for; every statement inside runs under that tenant.
    """

    approvals: ApprovalRepositoryPort
    audit: AuditRepositoryPort
    feedback: FeedbackRepositoryPort
    outbox: OutboxRepositoryPort
    # Consuming a single-use decision code in the decision's own transaction
    # (ADR 0007); only a decision admitted by a code touches it.
    decision_codes: DecisionCodeLedgerPort

    async def __aenter__(self) -> PlatformUnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class PlatformUnitOfWorkFactory(Protocol):
    """Creates a UoW bound to a verified AccessContext."""

    def __call__(self, context: AccessContext) -> PlatformUnitOfWork: ...
