"""The ``external_identities`` (provider ``zalo``) write and its one-time nonces.

The API (connect, status, disconnect) and the bot side (the worker's poll loop,
later the API webhook) link a user to their Zalo chat id through this repo, so
the "one Zalo per user, one user per Zalo" rule, the single-use token and the
row shape live here once rather than in each caller.

Both tables are identity plane with no RLS (see :mod:`identity_provisioning` and
migration ``02930a73bbdf``), so their statements need no tenant GUC; only the
audit and notification writes below bind one. Every caller binds this to
a ``dw_app`` session factory: ``dw_app`` holds SELECT/INSERT/UPDATE/DELETE on
``external_identities`` through the baseline grant, and on
``channel_link_nonces`` SELECT/INSERT/DELETE plus UPDATE of ``used_at`` alone
(the consume's WHERE and RETURNING read columns, which UPDATE alone does not allow).
``dw_provisioner`` is granted neither table and must not be used here.

Every link, relink and unlink is recorded in the same transaction that makes it,
because Z4 and Z5 build authority on this row: a code seen over someone's
shoulder must not let another chat act as its victim without a trace. For each
tenant the affected user belongs to, one ``platform.audit_events`` row (user,
channel, a hash of the chat id, who acted: the bot or the web) and one in-app
notification through ``platform.deliver_notification``, so the person sees
"Zalo vừa được kết nối" in their inbox whichever tenant they open. The user's
memberships are read through the baseline's ``memberships_self_select`` policy
(``app.principal_id``), the mechanism sign-in uses before a tenant is known;
each write then binds ``app.tenant_id`` to that membership's tenant. A link for a
user with no membership is refused: there is no tenant to record it in, and
nothing they could do with it until one is granted.

Structurally implements ``dw_connectors.adapters.zalo_link.ZaloLinkStore`` and
``ZaloAccountStore``; the Protocols are not imported so the platform keeps no
dependency on connectors. The token's claims arrive as any object with
``user_id``, ``jti`` and ``expires_at`` for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_kernel.channels import chat_reference
from dw_platform.adapters.persistence import tables
from dw_platform.adapters.persistence.notifications import DELIVER_NOTIFICATION

_ZALO = "zalo"
_nonces = tables.channel_link_nonces
_identities = tables.external_identities
_memberships = tables.memberships

_SET_PRINCIPAL = sa.text("SELECT set_config('app.principal_id', :principal_id, true)")
_SET_TENANT = sa.text("SELECT set_config('app.tenant_id', :tenant_id, true)")

# Who acted, recorded on every event: the bot (a chat sent /start or /stop) or
# the signed-in user on the web (the "Ngắt kết nối" button).
_BOT = "zalo_bot"
_WEB = "web"

_LINKED = "channel_link.linked"
_UNLINKED = "channel_link.unlinked"
# The chat sent another user's code, so it no longer belongs to this user.
_MOVED = "channel_link.moved_to_other_user"

_SETTINGS = "/settings"
_MESSAGES: dict[str, tuple[str, str]] = {
    _LINKED: (
        "Zalo vừa được kết nối với tài khoản của bạn",
        "Từ giờ thông báo và lệnh qua Zalo đi qua chat này. Nếu không phải bạn kết nối, "
        "vào Cài đặt cá nhân và bấm “Ngắt kết nối” ngay.",
    ),
    _UNLINKED: (
        "Zalo đã được ngắt khỏi tài khoản của bạn",
        "Bạn sẽ không nhận thông báo và không gửi lệnh qua Zalo nữa. Muốn dùng lại, "
        "vào Cài đặt cá nhân và bấm “Kết nối Zalo”.",
    ),
    _MOVED: (
        "Zalo của bạn đã chuyển sang tài khoản khác",
        "Chat Zalo từng kết nối với bạn vừa gửi mã kết nối của một tài khoản khác, nên tài "
        "khoản của bạn không còn kết nối Zalo. Nếu điều này bất thường, báo quản trị viên.",
    ),
}


class _NoTenantToRecordInError(Exception):
    """The user has no membership, so a link would have no tenant to be audited in."""


class LinkTokenClaims(Protocol):
    @property
    def user_id(self) -> UUID: ...

    @property
    def jti(self) -> str: ...

    @property
    def expires_at(self) -> datetime: ...


@dataclass(frozen=True)
class SqlZaloLink:
    session_factory: async_sessionmaker[AsyncSession]

    async def zalo_id_for(self, user_id: UUID) -> str | None:
        """The user's linked Zalo chat id, or None if they never connected."""
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    sa.select(_identities.c.subject)
                    .where(_identities.c.provider == _ZALO, _identities.c.user_id == user_id)
                    .limit(1)
                )
            ).first()
        return row[0] if row else None

    async def user_id_for(self, zalo_id: str) -> UUID | None:
        """The user this chat is linked to, or None — read afresh for every message.

        The chat-to-person step every inbound command starts from (ADR 0005
        condition 2). Not cached: an unlink between two messages must refuse
        the second.
        """
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    sa.select(_identities.c.user_id)
                    .where(_identities.c.provider == _ZALO, _identities.c.subject == zalo_id)
                    .limit(1)
                )
            ).first()
        return row[0] if row else None

    async def issue_nonce(self, token: LinkTokenClaims) -> None:
        """Record a freshly minted token so it can be redeemed exactly once."""
        async with self.session_factory() as session, session.begin():
            await session.execute(
                sa.insert(_nonces).values(
                    jti=token.jti,
                    channel=_ZALO,
                    user_id=token.user_id,
                    expires_at=token.expires_at,
                )
            )

    async def redeem(self, token: LinkTokenClaims, zalo_id: str) -> bool:
        """Consume the nonce and link, in one transaction; False if the nonce is spent.

        The consume is a single conditional UPDATE, never a read followed by a
        write: two ``/start`` with the same token in flight would both pass a
        read. The database's ``now()`` decides expiry, so a skewed app clock
        cannot stretch a token's life.
        """
        try:
            async with self.session_factory() as session, session.begin():
                consumed = (
                    await session.execute(
                        sa.update(_nonces)
                        .where(
                            _nonces.c.jti == token.jti,
                            _nonces.c.user_id == token.user_id,
                            _nonces.c.channel == _ZALO,
                            _nonces.c.used_at.is_(None),
                            _nonces.c.expires_at > sa.func.now(),
                        )
                        .values(used_at=sa.func.now())
                        .returning(_nonces.c.jti)
                    )
                ).first()
                if consumed is None:
                    return False
                await self._link(session, token.user_id, zalo_id)
        except _NoTenantToRecordInError:
            # Raised inside the transaction, so the consume rolls back with it.
            return False
        return True

    async def _link(self, session: AsyncSession, user_id: UUID, zalo_id: str) -> None:
        if not await self._record(session, user_id, _LINKED, zalo_id, _BOT):
            raise _NoTenantToRecordInError
        # One Zalo per user and one user per Zalo: clear both sides first so
        # re-linking (a user who switched Zalo, or a Zalo moved to another
        # user) never trips the unique constraints that back the rule.
        displaced = await session.execute(
            sa.delete(_identities)
            .where(
                _identities.c.provider == _ZALO,
                sa.or_(_identities.c.user_id == user_id, _identities.c.subject == zalo_id),
            )
            .returning(_identities.c.user_id)
        )
        for other in {row.user_id for row in displaced} - {user_id}:
            await self._record(session, other, _MOVED, zalo_id, _BOT)
        await session.execute(
            sa.insert(_identities).values(
                id=uuid4(), user_id=user_id, issuer=_ZALO, subject=zalo_id, provider=_ZALO
            )
        )

    async def unlink_by_zalo(self, zalo_id: str) -> bool:
        async with self.session_factory() as session, session.begin():
            removed = (
                await session.execute(
                    sa.delete(_identities)
                    .where(_identities.c.provider == _ZALO, _identities.c.subject == zalo_id)
                    .returning(_identities.c.user_id)
                )
            ).first()
            if removed is None:
                return False
            await self._record(session, removed.user_id, _UNLINKED, zalo_id, _BOT)
            return True

    async def unlink_by_user(self, user_id: UUID) -> None:
        async with self.session_factory() as session, session.begin():
            removed = (
                await session.execute(
                    sa.delete(_identities)
                    .where(_identities.c.provider == _ZALO, _identities.c.user_id == user_id)
                    .returning(_identities.c.subject)
                )
            ).first()
            if removed is not None:
                await self._record(session, user_id, _UNLINKED, removed.subject, _WEB)

    async def _record(
        self, session: AsyncSession, user_id: UUID, action: str, zalo_id: str, actor: str
    ) -> bool:
        """Audit and notify ``user_id`` once per tenant they belong to, in ``session``.

        False when they belong to none, so nothing was recorded. An unlink with
        nothing to record still stands: it only takes authority away.
        """
        await session.execute(_SET_PRINCIPAL, {"principal_id": str(user_id)})
        targets = (
            await session.execute(
                sa.select(_memberships.c.tenant_id, _memberships.c.workspace_id)
                .where(_memberships.c.user_id == user_id)
                .distinct(_memberships.c.tenant_id)
                .order_by(_memberships.c.tenant_id, _memberships.c.workspace_id)
            )
        ).all()
        event_id = uuid4()
        title, body = _MESSAGES[action]
        for tenant_id, workspace_id in targets:
            await session.execute(_SET_TENANT, {"tenant_id": str(tenant_id)})
            await session.execute(
                sa.insert(tables.audit_events).values(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    actor_id=user_id,
                    action=action,
                    resource_type="channel_link",
                    resource_id=str(user_id),
                    details={
                        "channel": _ZALO,
                        "chat_id_hash": chat_reference(_ZALO, zalo_id),
                        "actor": actor,
                        "event_id": str(event_id),
                    },
                    occurred_at=sa.func.now(),
                )
            )
            await session.execute(
                DELIVER_NOTIFICATION,
                {
                    "workspace_id": workspace_id,
                    "recipients": [user_id],
                    "source_key": f"{action}:{event_id}:{tenant_id}",
                    "title": title,
                    "body": body,
                    "link": _SETTINGS,
                },
            )
        return bool(targets)


@dataclass(frozen=True)
class SqlChannelLinkNonceRetention:
    """Implements ``dw_worker.consumers.retention.RetentionPrunePort``.

    Deletes nonces expired for more than a day, used or not. The day is a
    technical bound — long enough to answer "was this token used?" while
    debugging a failed link, short enough that the table stays tiny — not a
    legal term, so it lives here rather than in ``retention@*.yaml``.
    """

    session_factory: async_sessionmaker[AsyncSession]

    async def prune(self) -> None:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                sa.delete(_nonces).where(
                    _nonces.c.expires_at < sa.func.now() - sa.text("interval '1 day'")
                )
            )
