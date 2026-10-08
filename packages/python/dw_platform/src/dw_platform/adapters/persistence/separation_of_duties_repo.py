"""SQL implementation of the separation-of-duties repository.

The database decides whether a rule may be waived and whether a waiver may be
revoked (the triggers on `platform.sod_waivers`, migration 6b26771e549d). This
module only writes the rows under the caller's tenant RLS, puts the audit
event in the same transaction, and reads the database's refusals back as
domain errors by the constraint each one names.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.errors import ConflictError, NotFoundError
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.repositories import SqlAuditRepository
from dw_platform.adapters.persistence.separation_of_duties import refusal
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session
from dw_platform.application.access_context import AccessContext
from dw_platform.application.separation_of_duties import SodRuleStatus, SodWaiver
from dw_platform.domain.audit import AuditEvent

_NOT_WAIVABLE = "ck_sod_waivers_rule_waivable"
_ALREADY_OPEN = "uq_sod_waivers_open"
_IN_USE = "ck_sod_waivers_not_in_use"
_SECOND_PERSON = "ck_sod_waivers_second_person"


@dataclass(frozen=True)
class SqlSeparationOfDutiesRepository:
    session_factory: async_sessionmaker[AsyncSession]

    async def list_rules(self, context: AccessContext) -> list[SodRuleStatus]:
        rules, waivers = tables.sod_rules, tables.sod_waivers
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            rows = (
                await session.execute(
                    sa.select(
                        rules.c.key,
                        rules.c.description,
                        rules.c.left_scopes,
                        rules.c.right_scopes,
                        rules.c.waivable,
                        waivers.c.reason,
                        waivers.c.granted_by,
                        waivers.c.granted_at,
                        waivers.c.confirmed_by,
                        waivers.c.confirmed_at,
                    )
                    .select_from(
                        rules.outerjoin(
                            waivers,
                            sa.and_(
                                waivers.c.rule_key == rules.c.key,
                                waivers.c.tenant_id == context.tenant_id,
                                waivers.c.revoked_at.is_(None),
                            ),
                        )
                    )
                    .order_by(rules.c.key)
                )
            ).all()
        return [
            SodRuleStatus(
                key=row.key,
                description=row.description,
                left_scopes=tuple(row.left_scopes),
                right_scopes=tuple(row.right_scopes),
                waivable=row.waivable,
                waiver=None
                if row.granted_at is None
                else SodWaiver(
                    reason=row.reason,
                    granted_by=row.granted_by,
                    granted_at=row.granted_at,
                    confirmed_by=row.confirmed_by,
                    confirmed_at=row.confirmed_at,
                ),
            )
            for row in rows
        ]

    async def waive(
        self,
        context: AccessContext,
        *,
        waiver_id: uuid.UUID,
        rule_key: str,
        reason: str,
        audit: AuditEvent,
    ) -> None:
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            known = (
                await session.execute(
                    sa.select(tables.sod_rules.c.key).where(tables.sod_rules.c.key == rule_key)
                )
            ).first()
            if known is None:
                raise NotFoundError("no such rule", details={"rule_key": rule_key})
            try:
                await session.execute(
                    sa.insert(tables.sod_waivers).values(
                        id=waiver_id,
                        tenant_id=context.tenant_id,
                        rule_key=rule_key,
                        reason=reason,
                        granted_by=context.principal_id,
                    )
                )
            except IntegrityError as exc:
                refused = refusal(exc)
                if refused is not None and refused[0] == _NOT_WAIVABLE:
                    raise ConflictError(
                        "this rule cannot be waived", details={"rule_key": rule_key}
                    ) from exc
                if refused is not None and refused[0] == _ALREADY_OPEN:
                    raise ConflictError(
                        "this rule is already waived", details={"rule_key": rule_key}
                    ) from exc
                raise
            await SqlAuditRepository(session).append(audit)

    async def revoke(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        waivers = tables.sod_waivers
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            try:
                row = (
                    await session.execute(
                        sa.update(waivers)
                        .where(
                            waivers.c.tenant_id == context.tenant_id,
                            waivers.c.rule_key == rule_key,
                            waivers.c.revoked_at.is_(None),
                        )
                        .values(
                            revoked_at=sa.func.now(),
                            revoked_by=context.principal_id,
                            revoke_reason=reason,
                        )
                        .returning(waivers.c.id)
                    )
                ).first()
            except IntegrityError as exc:
                refused = refusal(exc)
                if refused is not None and refused[0] == _IN_USE:
                    raise ConflictError(
                        "memberships still hold both sides of this rule; change their"
                        " roles before revoking the waiver",
                        details={"rule_key": rule_key, "memberships": int(refused[1] or 0)},
                    ) from exc
                raise
            if row is None:
                return False
            await SqlAuditRepository(session).append(audit)
        return True

    async def confirm(
        self, context: AccessContext, *, rule_key: str, reason: str, audit: AuditEvent
    ) -> bool:
        waivers = tables.sod_waivers
        async with tenant_session(
            self.session_factory, TenantScope.from_access_context(context)
        ) as session:
            try:
                row = (
                    await session.execute(
                        sa.update(waivers)
                        .where(
                            waivers.c.tenant_id == context.tenant_id,
                            waivers.c.rule_key == rule_key,
                            waivers.c.revoked_at.is_(None),
                            waivers.c.confirmed_at.is_(None),
                        )
                        .values(
                            confirmed_at=sa.func.now(),
                            confirmed_by=context.principal_id,
                            confirm_reason=reason,
                        )
                        .returning(waivers.c.id)
                    )
                ).first()
            except IntegrityError as exc:
                refused = refusal(exc)
                if refused is not None and refused[0] == _SECOND_PERSON:
                    raise ConflictError(
                        "a waiver is confirmed by a second person, not by who proposed it",
                        details={"rule_key": rule_key},
                    ) from exc
                raise
            if row is None:
                return False
            await SqlAuditRepository(session).append(audit)
        return True
