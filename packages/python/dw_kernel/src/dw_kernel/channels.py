"""How an audit record names a chat without holding its address.

A chat id is where a bot's replies go: whoever reads it can message that person.
The audit trail needs to say WHICH chat acted — the one that linked an account,
the one that proposed a product — and to let two records about the same chat be
matched, without carrying the address. One formula for every writer, so a link
and the proposal that chat later sent name the chat the same way.
"""

from __future__ import annotations

from hashlib import sha256


def chat_reference(channel: str, chat_id: str) -> str:
    """A stable reference to a chat for the audit trail, not the address itself."""
    return sha256(f"{channel}:{chat_id}".encode()).hexdigest()[:16]
