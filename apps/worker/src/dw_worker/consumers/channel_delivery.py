"""Channel delivery: in-app notifications out through a linked chat (ADR 0006).

``platform.deliver_notification`` queues a row in ``platform.channel_deliveries``
for every recipient with a chat link, in the transaction that addresses the
notification. This lane sends them. Per tick it asks which tenant/workspace
scopes have due rows, then claims them one at a time, each in its own
transaction bound to that scope, the row locked (``FOR UPDATE SKIP LOCKED``)
while it is sent:

1. the recipient must still belong to the workspace, in an active tenant, or
   the row is ``cancelled`` (``recipient_not_member``);
2. the chat address is read now, not when the row was queued: no link any
   more, ``cancelled`` (``recipient_unlinked``), and nothing is sent;
3. the provider says the chat cannot be reached
   (``ChatRecipientUnreachableError``): ``failed`` after one attempt;
4. any other failure: one attempt more, due again after a delay that doubles,
   and ``failed`` (``attempts_exhausted``) at ``MAX_ATTEMPTS``;
5. sent: ``sent``, with the provider's message id.

Every outcome but a retry is audited once. Delivery is at least once: a
process that dies between the send and the commit sends that one message again.

What the message says is ``compose``'s alone: the tenant and workspace names,
the notification's title and an absolute link to the portal. Never its body,
and so never a decision code or a secret (ADR 0006).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from typing import Protocol

from dw_connectors.ports import ChatRecipientUnreachableError, ChatSenderPort
from dw_kernel.channels import chat_reference

logger = logging.getLogger("dw_worker.channel_delivery")

# Five attempts, then failed (channels Z2). The delays between them double from
# BASE_DELAY: 1, 2, 4 and 8 minutes, so a provider outage of a quarter of an
# hour is ridden out and a dead chat is given up on the same morning.
MAX_ATTEMPTS = 5
BASE_DELAY = timedelta(minutes=1)
# Rows claimed per scope per tick, so one busy workspace cannot starve the
# others of a tick; the rest wait for the next.
BATCH_PER_SCOPE = 20


def backoff(attempts_made: int) -> timedelta:
    """The wait before the next attempt, after ``attempts_made`` failed ones."""
    return BASE_DELAY * int(2 ** (attempts_made - 1))


def compose(
    *, tenant_name: str, workspace_name: str, title: str, link: str | None, web_url: str
) -> str:
    """Plain text: where it happened, what, and where to look. Nothing else."""
    return f"[{tenant_name} · {workspace_name}]\n{title}\n{web_url.rstrip('/')}{link or '/'}"


class DeliveryScopeRef(Protocol):
    @property
    def tenant_id(self) -> uuid.UUID: ...

    @property
    def workspace_id(self) -> uuid.UUID: ...


class ClaimedDelivery(Protocol):
    """One due row, locked in an open transaction until settled."""

    @property
    def recipient_user_id(self) -> uuid.UUID: ...

    @property
    def title(self) -> str: ...

    @property
    def link(self) -> str | None: ...

    @property
    def attempts(self) -> int: ...

    @property
    def tenant_name(self) -> str: ...

    @property
    def workspace_name(self) -> str: ...

    async def recipient_may_receive(self) -> bool: ...

    async def mark_sent(self, external_message_id: str, chat_reference: str) -> None: ...

    async def mark_failed(self, reason: str, error: str) -> None: ...

    async def mark_cancelled(self, reason: str) -> None: ...

    async def retry_later(self, error: str, delay: timedelta) -> None: ...


class ChannelOutboxPort(Protocol):
    """``dw_platform.adapters.persistence.channel_deliveries.SqlChannelOutbox``."""

    async def due_scopes(self, channel: str) -> Sequence[DeliveryScopeRef]: ...

    def claim_next(
        self, channel: str, scope: DeliveryScopeRef
    ) -> AbstractAsyncContextManager[ClaimedDelivery | None]: ...


# The chat a person linked on this channel, read at send time; None if none.
AddressLookup = Callable[[uuid.UUID], Awaitable[str | None]]


def _error_text(exc: BaseException) -> str:
    # The adapters scrub their own messages (the Zalo token, SEC-20); the class
    # name keeps an empty message readable.
    return f"{type(exc).__name__}: {exc}"


async def _deliver(
    claim: ClaimedDelivery,
    *,
    channel: str,
    address_of: AddressLookup,
    sender: ChatSenderPort,
    web_url: str,
) -> None:
    if not await claim.recipient_may_receive():
        await claim.mark_cancelled("recipient_not_member")
        return
    chat = await address_of(claim.recipient_user_id)
    if chat is None:
        await claim.mark_cancelled("recipient_unlinked")
        return
    text = compose(
        tenant_name=claim.tenant_name,
        workspace_name=claim.workspace_name,
        title=claim.title,
        link=claim.link,
        web_url=web_url,
    )
    try:
        message_id = await sender.send_message(chat, text)
    except ChatRecipientUnreachableError as exc:
        await claim.mark_failed("recipient_unreachable", _error_text(exc))
        return
    except Exception as exc:
        # Broad on purpose: every failure a provider has not named permanent is
        # worth another try, up to the ceiling.
        made = claim.attempts + 1
        if made >= MAX_ATTEMPTS:
            await claim.mark_failed("attempts_exhausted", _error_text(exc))
        else:
            await claim.retry_later(_error_text(exc), backoff(made))
        return
    await claim.mark_sent(message_id, chat_reference(channel, chat))


def build_channel_delivery_consumer(
    outbox: ChannelOutboxPort,
    *,
    channel: str,
    address_of: AddressLookup,
    sender: ChatSenderPort,
    web_url: str,
    batch_per_scope: int = BATCH_PER_SCOPE,
) -> Callable[[], Awaitable[None]]:
    async def consume() -> None:
        for scope in await outbox.due_scopes(channel):
            try:
                for _ in range(batch_per_scope):
                    async with outbox.claim_next(channel, scope) as claim:
                        if claim is None:
                            break
                        await _deliver(
                            claim,
                            channel=channel,
                            address_of=address_of,
                            sender=sender,
                            web_url=web_url,
                        )
            except Exception:
                # One scope's database trouble must not hold up the others; the
                # row it was on rolled back to pending. Logged without ids that
                # would name a person.
                logger.exception("channel delivery: a scope failed; the next tick retries")

    return consume
