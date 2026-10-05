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
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.follow_up import FollowUpKind

__all__ = ["SupplyChainFollowUpPolicy", "load_supply_chain_follow_up_policy"]

_SCOPE = re.compile(r"^supply_chain\.[a-z_]+(\.[a-z_]+)*$")


class SupplyChainFollowUpPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    recipients: dict[FollowUpKind, tuple[str, ...]]

    @model_validator(mode="after")
    def _every_kind_reaches_someone(self) -> SupplyChainFollowUpPolicy:
        missing = [kind.value for kind in FollowUpKind if not self.recipients.get(kind)]
        if missing:
            raise ValueError(f"every follow-up kind needs at least one recipient scope: {missing}")
        foreign = sorted(
            {
                scope
                for scopes in self.recipients.values()
                for scope in scopes
                if not _SCOPE.fullmatch(scope)
            }
        )
        if foreign:
            raise ValueError(f"recipient scopes must be Supply Chain scopes: {foreign}")
        return self

    def recipient_scopes(self, kind: FollowUpKind) -> frozenset[str]:
        return frozenset(self.recipients[kind])


def load_supply_chain_follow_up_policy(path: Path) -> SupplyChainFollowUpPolicy:
    return SupplyChainFollowUpPolicy.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
