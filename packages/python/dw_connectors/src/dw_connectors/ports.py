"""Connector ports implemented by provider adapters and the mock."""

from __future__ import annotations

from typing import Protocol

from dw_connectors.contracts import CreateExternalTask, ExternalTaskRef


class TaskConnectorPort(Protocol):
    """Creates tasks in an external work-management system.

    Implementations MUST be idempotent on ``idempotency_key``: retrying the
    same key returns the original reference and never creates a duplicate.
    """

    @property
    def connector_name(self) -> str: ...

    async def create_task(
        self,
        command: CreateExternalTask,
        idempotency_key: str,
    ) -> ExternalTaskRef: ...


class ChatRecipientUnreachableError(Exception):
    """The channel will never deliver to this conversation: it does not exist,
    or the person blocked the bot. Retrying cannot help, so a sender that keeps
    an outbox marks the delivery failed instead of trying again.

    Any other exception from ``send_message`` is treated as transient. The
    message carries no address and no credential.
    """


class ChatSenderPort(Protocol):
    """Sends one plain-text message into a conversation.

    Deliberately the narrow intersection of every channel rather than the union:
    a richer provider's vocabulary (rich blocks, a thread timestamp, reactions)
    would force every plainer adapter to explain what it cannot do. Code that
    needs a provider's own features takes that provider's client, and says so
    in its type.

    The id is whatever the channel calls a conversation - a Zalo chat, a Teams
    thread. The return is the provider's message id, best effort, for code
    that later edits or deletes what it sent.

    Raises ``ChatRecipientUnreachableError`` when the provider says the
    conversation cannot be reached; anything else it raises may be retried.
    """

    async def send_message(self, conversation_id: str, text: str) -> str: ...
