"""Who else works in this workspace.

Every CRM record carries an owner, and every task an assignee, as a bare user
id. Rendering those ids as people - and offering a picker that sets them - needs
one list: the members of the workspace in context. It lives in the platform
because membership is a platform fact. A bounded context that kept its own copy
of "who works here" would drift the first time somebody joined or left, and the
drift would show up as a task assigned to a name nobody recognises.

The read model carries display fields and nothing else. Roles travel with it
because an owner picker distinguishes a salesperson from a manager; scopes,
clearance and the plan deliberately do not, because what a colleague is allowed
to do is not the picker's business.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceMember:
    """One person in the workspace, as the UI needs to show them."""

    user_id: uuid.UUID
    display_name: str
    email: str | None
    role_keys: tuple[str, ...]
    permission_set_keys: tuple[str, ...]
    department: str
    # `invited` until the first sign-in links an identity, then `active`.
    status: str = "active"


@dataclass(frozen=True, slots=True)
class IdentityRef:
    """A signed-in identity the admin can pick to grant, instead of typing the
    email by hand. Just enough to show and select."""

    user_id: uuid.UUID
    display_name: str
    email: str | None
