"""SQL repositories mapping platform tables <-> domain objects.

All queries run inside a UoW session that already carries the SET LOCAL tenant
context, so RLS constrains every statement here.

RLS on `approval_requests` and `audit_events` narrows by tenant only. Their
reads take the caller's `workspace_id` as a required keyword and narrow by it
here, so a member never reads another workspace's approval payload or audit
detail (platform-runtime/approval-audit-and-workspace/02). Required, not
defaulted: a reader that forgets it does not type-check.

Approval reads also take the caller's `ApprovalAudience` and filter by it in
the query (`visible_to`): a stamped request is absent to whoever may neither
decide it nor asked for it (ADR 0004, amendment 2026-10-07).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult, Row
from sqlalchemy.ext.asyncio import AsyncSession

from dw_kernel.errors import ConflictError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.keyset import after_position, newest_first
from dw_platform.application.authorization import ApprovalAudience
from dw_platform.domain.approval import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
)
from dw_platform.domain.audit import AuditEvent
from dw_platform.domain.feedback import Feedback, FeedbackAttachment
from dw_platform.domain.outbox import OutboxEvent


def _approval_from_row(row: Row[tuple]) -> ApprovalRequest:  # type: ignore[type-arg]
    return ApprovalRequest(
        id=row.id,
        tenant_id=TenantId(row.tenant_id),
        workspace_id=WorkspaceId(row.workspace_id),
        approval_type=row.approval_type,
        requested_by=UserId(row.requested_by),
        reason=row.reason,
        payload=dict(row.payload),
        run_id=row.run_id,
        required_scope=row.required_scope,
        status=ApprovalStatus(row.status),
        created_at=row.created_at,
        decided_at=row.decided_at,
        version=row.version,
    )


def visible_to(audience: ApprovalAudience) -> sa.ColumnElement[bool]:
    """`ApprovalAudience.may_see`, as a WHERE clause: unstamped, or asked for by
    the caller, or stamped with a scope the caller holds while also holding
    `approvals.decide`. Public because the context-facing pending query reads
    the same rule; a second copy of it would drift (failure-modes #2)."""
    approvals = tables.approval_requests
    decidable = (
        approvals.c.required_scope.in_(sorted(audience.context.scopes))
        if audience.holds_decide
        else sa.false()
    )
    return sa.or_(
        approvals.c.required_scope.is_(None),
        approvals.c.requested_by == audience.context.principal_id,
        decidable,
    )


def _approval_position(request: ApprovalRequest) -> CursorPosition:
    # ``created_at`` is None only on an aggregate that has not been inserted yet
    # — the column default fills it — and this only ever sees rows read back.
    assert request.created_at is not None
    return CursorPosition(sort_value=request.created_at, tiebreaker=request.id)


def outbox_from_row(row: Row[tuple]) -> OutboxEvent:  # type: ignore[type-arg]
    """Public because the worker-side drain maps the same table without a tenant."""
    return OutboxEvent(
        id=row.id,
        tenant_id=TenantId(row.tenant_id),
        workspace_id=WorkspaceId(row.workspace_id),
        event_type=row.event_type,
        schema_version=row.schema_version,
        aggregate_id=row.aggregate_id,
        occurred_at=row.occurred_at,
        payload=dict(row.payload),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        actor_id=row.actor_id,
        processed_at=row.processed_at,
        attempts=row.attempts,
        last_error=row.last_error,
    )


@dataclass
class SqlApprovalRepository:
    """Implements ``ApprovalRepositoryPort``."""

    session: AsyncSession

    async def add(self, request: ApprovalRequest) -> None:
        await self.session.execute(
            sa.insert(tables.approval_requests).values(
                id=request.id,
                tenant_id=request.tenant_id.value,
                workspace_id=request.workspace_id.value,
                approval_type=request.approval_type,
                requested_by=request.requested_by.value,
                reason=request.reason,
                payload=request.payload,
                run_id=request.run_id,
                # Written here and nowhere else: `save` records a decision and
                # must never move who was allowed to make it (ADR 0004).
                required_scope=request.required_scope,
                status=request.status.value,
                version=request.version,
            )
        )

    async def get(
        self, request_id: uuid.UUID, *, workspace_id: uuid.UUID, audience: ApprovalAudience
    ) -> ApprovalRequest | None:
        result = await self.session.execute(
            sa.select(tables.approval_requests).where(
                tables.approval_requests.c.id == request_id,
                # RLS narrows this table by tenant only; the workspace is
                # narrowed here (approval-audit-and-workspace/02).
                tables.approval_requests.c.workspace_id == workspace_id,
                visible_to(audience),
            )
        )
        row = result.first()
        return _approval_from_row(row) if row else None

    async def save(self, request: ApprovalRequest) -> None:
        result = await self.session.execute(
            sa.update(tables.approval_requests)
            .where(
                tables.approval_requests.c.id == request.id,
                tables.approval_requests.c.version == request.version - 1,
            )
            .values(
                status=request.status.value,
                decided_at=request.decided_at,
                version=request.version,
            )
        )
        assert isinstance(result, CursorResult)
        if result.rowcount != 1:
            raise ConflictError(
                "approval request was modified concurrently",
                details={"request_id": str(request.id)},
            )

    async def list_pending(
        self, request: PageRequest, *, workspace_id: uuid.UUID, audience: ApprovalAudience
    ) -> Page[ApprovalRequest]:
        result = await self.session.execute(
            sa.select(tables.approval_requests)
            .where(
                tables.approval_requests.c.workspace_id == workspace_id,
                tables.approval_requests.c.status == "pending",
                # In the query, so a page is a page of the caller's inbox and
                # never a page of everyone's with holes in it.
                visible_to(audience),
                after_position(
                    tables.approval_requests.c.created_at,
                    tables.approval_requests.c.id,
                    request.after,
                ),
            )
            .order_by(
                *newest_first(tables.approval_requests.c.created_at, tables.approval_requests.c.id)
            )
            .limit(request.fetch_limit)
        )
        return build_page(
            [_approval_from_row(row) for row in result],
            request=request,
            position_of=_approval_position,
        )

    async def add_decision(self, decision: ApprovalDecision) -> None:
        await self.session.execute(
            sa.insert(tables.approval_decisions).values(
                id=decision.id,
                request_id=decision.request_id,
                tenant_id=decision.tenant_id.value,
                workspace_id=decision.workspace_id.value,
                decided_by=decision.decided_by.value,
                outcome=decision.outcome.value,
                comment=decision.comment,
                decided_at=decision.decided_at,
                channel=decision.channel,
            )
        )


@dataclass
class SqlAuditRepository:
    """Implements ``AuditRepositoryPort``; append-only by grants."""

    session: AsyncSession

    async def append(self, event: AuditEvent) -> None:
        await self.session.execute(
            sa.insert(tables.audit_events).values(
                id=event.id,
                tenant_id=event.tenant_id.value,
                workspace_id=event.workspace_id.value,
                actor_id=event.actor_id.value,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                run_id=event.run_id,
                policy_decision=event.policy_decision,
                trace_id=event.trace_id,
                details=event.details,
                occurred_at=event.occurred_at,
            )
        )

    async def list_for_run(
        self, run_id: uuid.UUID, *, workspace_id: uuid.UUID, limit: int = 100
    ) -> list[AuditEvent]:
        result = await self.session.execute(
            sa.select(tables.audit_events)
            .where(
                tables.audit_events.c.run_id == run_id,
                tables.audit_events.c.workspace_id == workspace_id,
            )
            .order_by(tables.audit_events.c.occurred_at)
            .limit(limit)
        )
        return self._map_rows(result)

    async def list_page(self, request: PageRequest, *, workspace_id: uuid.UUID) -> Page[AuditEvent]:
        result = await self.session.execute(
            sa.select(tables.audit_events)
            .where(
                tables.audit_events.c.workspace_id == workspace_id,
                after_position(
                    tables.audit_events.c.occurred_at, tables.audit_events.c.id, request.after
                ),
            )
            .order_by(*newest_first(tables.audit_events.c.occurred_at, tables.audit_events.c.id))
            .limit(request.fetch_limit)
        )
        return build_page(
            self._map_rows(result),
            request=request,
            position_of=lambda event: CursorPosition(
                sort_value=event.occurred_at, tiebreaker=event.id
            ),
        )

    @staticmethod
    def _map_rows(result: sa.engine.Result[tuple]) -> list[AuditEvent]:  # type: ignore[type-arg]
        return [
            AuditEvent(
                id=row.id,
                tenant_id=TenantId(row.tenant_id),
                workspace_id=WorkspaceId(row.workspace_id),
                actor_id=UserId(row.actor_id),
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                run_id=row.run_id,
                policy_decision=row.policy_decision,
                trace_id=row.trace_id,
                details=dict(row.details),
                occurred_at=row.occurred_at,
            )
            for row in result
        ]


@dataclass
class SqlFeedbackRepository:
    """Implements ``FeedbackRepositoryPort``.

    Runs inside the tenant-scoped UoW, so RLS confines every row to the caller's
    tenant: a member's ``add`` can only land in their own tenant, and the admin
    inbox ``list_recent`` can only read that tenant's rows.
    """

    session: AsyncSession

    async def add(
        self,
        *,
        feedback_id: uuid.UUID,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        author_id: uuid.UUID,
        category: str,
        message: str,
        module: str | None = None,
        page_path: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        # created_at is left to the column default (one clock: the database).
        await self.session.execute(
            sa.insert(tables.feedback).values(
                id=feedback_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                author_id=author_id,
                category=category,
                message=message,
                module=module,
                page_path=page_path,
                suggestion=suggestion,
            )
        )

    async def add_attachment(
        self,
        *,
        attachment_id: uuid.UUID,
        feedback_id: uuid.UUID,
        tenant_id: uuid.UUID,
        workspace_id: uuid.UUID,
        object_key: str,
        content_type: str,
        size_bytes: int,
    ) -> None:
        await self.session.execute(
            sa.insert(tables.feedback_attachments).values(
                id=attachment_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                feedback_id=feedback_id,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )

    async def get_attachment(
        self, feedback_id: uuid.UUID, attachment_id: uuid.UUID
    ) -> FeedbackAttachment | None:
        row = (
            await self.session.execute(
                sa.select(tables.feedback_attachments).where(
                    tables.feedback_attachments.c.id == attachment_id,
                    tables.feedback_attachments.c.feedback_id == feedback_id,
                )
            )
        ).one_or_none()
        return _to_attachment(row) if row is not None else None

    async def list_page(self, request: PageRequest) -> Page[Feedback]:
        result = await self.session.execute(
            sa.select(
                tables.feedback,
                tables.users.c.display_name.label("author_name"),
            )
            .select_from(
                tables.feedback.join(tables.users, tables.feedback.c.author_id == tables.users.c.id)
            )
            .where(
                after_position(tables.feedback.c.created_at, tables.feedback.c.id, request.after)
            )
            .order_by(*newest_first(tables.feedback.c.created_at, tables.feedback.c.id))
            .limit(request.fetch_limit)
        )
        # Trimmed before the attachment query so the over-fetched probe row does
        # not drag its screenshots back with it.
        page = build_page(
            result.all(),
            request=request,
            position_of=lambda row: CursorPosition(sort_value=row.created_at, tiebreaker=row.id),
        )
        rows = page.items
        # One more statement for every screenshot of the page, grouped here,
        # rather than one per feedback (no N+1 on the inbox).
        by_feedback: dict[uuid.UUID, list[FeedbackAttachment]] = {row.id: [] for row in rows}
        if by_feedback:
            attachments = await self.session.execute(
                sa.select(tables.feedback_attachments)
                .where(tables.feedback_attachments.c.feedback_id.in_(list(by_feedback)))
                .order_by(tables.feedback_attachments.c.created_at)
            )
            for attachment in attachments:
                by_feedback[attachment.feedback_id].append(_to_attachment(attachment))
        return page.map_items(
            lambda row: Feedback(
                id=row.id,
                tenant_id=TenantId(row.tenant_id),
                workspace_id=WorkspaceId(row.workspace_id),
                author_id=UserId(row.author_id),
                author_name=row.author_name,
                category=row.category,
                message=row.message,
                created_at=row.created_at,
                module=row.module,
                page_path=row.page_path,
                suggestion=row.suggestion,
                attachments=tuple(by_feedback[row.id]),
            )
        )


def _to_attachment(row: sa.Row[Any]) -> FeedbackAttachment:
    return FeedbackAttachment(
        id=row.id,
        feedback_id=row.feedback_id,
        object_key=row.object_key,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


@dataclass
class SqlOutboxRepository:
    """Implements ``OutboxRepositoryPort``."""

    session: AsyncSession

    async def add(self, event: OutboxEvent) -> None:
        await self.session.execute(
            sa.insert(tables.outbox_events).values(
                id=event.id,
                tenant_id=event.tenant_id.value,
                workspace_id=event.workspace_id.value,
                event_type=event.event_type,
                schema_version=event.schema_version,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                actor_id=event.actor_id,
                occurred_at=event.occurred_at,
                processed_at=event.processed_at,
                attempts=event.attempts,
                last_error=event.last_error,
            )
        )

    async def list_unprocessed(self, limit: int = 100) -> list[OutboxEvent]:
        result = await self.session.execute(
            sa.select(tables.outbox_events)
            .where(tables.outbox_events.c.processed_at.is_(None))
            .order_by(tables.outbox_events.c.occurred_at)
            .limit(limit)
        )
        return [outbox_from_row(row) for row in result]

    async def has_unprocessed(self, event_type: str, aggregate_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            sa.select(sa.literal(True))
            .select_from(tables.outbox_events)
            .where(
                tables.outbox_events.c.processed_at.is_(None),
                tables.outbox_events.c.event_type == event_type,
                tables.outbox_events.c.aggregate_id == aggregate_id,
            )
            .limit(1)
        )
        return result.scalar() is not None
