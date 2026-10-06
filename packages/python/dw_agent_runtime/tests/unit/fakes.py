"""In-memory doubles for the platform ports the runtime writes through."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from dw_kernel.pagination import CursorPosition, Page, PageRequest, build_page
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PlatformUnitOfWork
from dw_platform.domain.audit import AuditEvent

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


@dataclass
class FakeExecutionStore:
    succeeded: dict[tuple[str, str], Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)

    async def find_succeeded(
        self, run_context: Any, tool_name: str, idempotency_key: str
    ) -> Any | None:
        return self.succeeded.get((tool_name, idempotency_key))

    async def record(self, run_context: Any, **kwargs: Any) -> None:
        self.records.append(kwargs)
        if kwargs.get("status") == "succeeded" and kwargs.get("idempotency_key"):
            entry = type(
                "Stored",
                (),
                {"input_hash": kwargs["input_hash"], "output": kwargs["output"]},
            )()
            self.succeeded[(kwargs["tool_name"], kwargs["idempotency_key"])] = entry


@dataclass
class FakeAuditRepo:
    events: list[AuditEvent] = field(default_factory=list)

    async def append(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def list_page(self, request: PageRequest, *, workspace_id: UUID) -> Page[AuditEvent]:
        # Newest first and the caller's workspace only, like the SQL repository
        # this stands in for.
        return build_page(
            [event for event in reversed(self.events) if event.workspace_id.value == workspace_id],
            request=request,
            position_of=lambda event: CursorPosition(
                sort_value=event.occurred_at, tiebreaker=event.id
            ),
        )


@dataclass
class FakeUoW:
    audit: FakeAuditRepo
    approvals: Any = None
    outbox: Any = None

    async def __aenter__(self) -> FakeUoW:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


@dataclass
class FakeUoWFactory:
    audit_repo: FakeAuditRepo = field(default_factory=FakeAuditRepo)

    def __call__(self, context: AccessContext) -> PlatformUnitOfWork:
        return cast(PlatformUnitOfWork, FakeUoW(audit=self.audit_repo))
