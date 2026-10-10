"""Who is handed each kind of follow-up, per tenant.

Placed at the package's top level, like `sla_policy.py` and `brief_policy.py`:
a versioned artifact's schema and parser, touching the filesystem.

Recipients are named by scope, not by role or person: whoever in the case's
workspace holds any listed scope, through a role or a permission set, is
handed the follow-up and notified. That keeps routing on the same footing as
authorization (NIST RBAC: duties are scopes), survives staff changes, and is
stamped on each follow-up when it opens, so a later edit to the policy does
not re-route work already handed out.

Every kind must be routed to someone: a follow-up nobody receives is an
escalation nobody sees, so a document that leaves a kind out, or lists no
scope for it, is refused.

1.1 (stage-1 ticket 06, ADR 0019) adds one recipient that is not a scope:
`pic`, the case's PIC. It is stamped on the follow-up as a person when it
opens, and only when the case has a PIC who is still a member of the case's
workspace. A kind naming `pic` must still name a scope: a PIC who is absent,
has left, or a case opened before PICs existed would otherwise leave the
follow-up with nobody (QE-18, provisional: the scopes are the escalation). A
1.0 document, which cannot name `pic`, stays valid.

1.2 (ticket P3) adds `closed_retention_days`: how long a closed follow-up
(`done` or `resolved`) is kept after it closed before the worker's retention
lane deletes it; an open one is never deleted. A 1.2 document must name it, an
earlier one cannot. The platform document's term is the default and the floor:
`follow_up_retention_days` gives a tenant its own term only when it is longer
(failure-modes #7: a shortened history is the irreversible direction). A 1.0 or
1.1 override stored before the term existed keeps the platform's.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.follow_up import FollowUpKind

__all__ = [
    "PIC_RECIPIENT",
    "SupplyChainFollowUpPolicy",
    "follow_up_retention_days",
    "load_supply_chain_follow_up_policy",
]

_SCOPE = re.compile(r"^supply_chain\.[a-z_]+(\.[a-z_]+)*$")
# The one recipient that is a person, not a scope (1.1).
PIC_RECIPIENT = "pic"


class SupplyChainFollowUpPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.[012]$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    recipients: dict[FollowUpKind, tuple[str, ...]]
    # Days a closed follow-up is kept after it closed (1.2). One day to ten
    # years: the lower bound is also the database function's own.
    closed_retention_days: int | None = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def _term_from_1_2(self) -> SupplyChainFollowUpPolicy:
        names_term = self.closed_retention_days is not None
        if names_term != (self.schema_version == "1.2"):
            raise ValueError("closed_retention_days is required from schema 1.2 and refused before")
        return self

    @model_validator(mode="after")
    def _every_kind_reaches_someone(self) -> SupplyChainFollowUpPolicy:
        missing = [kind.value for kind in FollowUpKind if not self.recipient_scopes(kind)]
        if missing:
            raise ValueError(f"every follow-up kind needs at least one recipient scope: {missing}")
        pic_allowed = self.schema_version != "1.0"
        foreign = sorted(
            {
                scope
                for scopes in self.recipients.values()
                for scope in scopes
                if not _SCOPE.fullmatch(scope) and not (pic_allowed and scope == PIC_RECIPIENT)
            }
        )
        if foreign:
            raise ValueError(f"recipient scopes must be Supply Chain scopes: {foreign}")
        return self

    def recipient_scopes(self, kind: FollowUpKind) -> frozenset[str]:
        """The scopes a kind is handed to, `pic` left out."""
        return frozenset(self.recipients.get(kind, ())) - {PIC_RECIPIENT}

    def routes_to_pic(self, kind: FollowUpKind) -> bool:
        return PIC_RECIPIENT in self.recipients.get(kind, ())


def follow_up_retention_days(
    tenant: SupplyChainFollowUpPolicy, platform: SupplyChainFollowUpPolicy
) -> int:
    """Days a closed follow-up of this tenant is kept: the tenant's own term
    when it is longer than the platform's, the platform's otherwise. The
    platform document must name a term; one that does not is refused, never
    read as "keep nothing" or "keep forever"."""
    floor = platform.closed_retention_days
    if floor is None:
        raise ValueError("the platform follow-up policy names no retention term")
    return max(tenant.closed_retention_days or floor, floor)


def load_supply_chain_follow_up_policy(path: Path) -> SupplyChainFollowUpPolicy:
    return SupplyChainFollowUpPolicy.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
