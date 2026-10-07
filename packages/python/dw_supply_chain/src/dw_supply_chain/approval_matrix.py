"""Which `CaseAction`s pause a PO case for human approval, per tenant.

Placed at the package's top level rather than under `domain/`, same
reasoning as `sla_policy.py`: this is a versioned artifact's schema and
parser, not a business rule about a `POCase`, and it touches the filesystem
(`load_supply_chain_approval_matrix` reads a YAML file), which domain code
must not do.

`approval_required_actions` is typed `frozenset[CaseAction]`, not
`frozenset[str]`: a tenant's submitted matrix is validated against the real,
closed action set at the schema boundary, the same "narrow default, no
silently-dead entry" discipline CLAUDE.md asks for — a typo'd action name is
refused at write time, never a config that reads like a safeguard and gates
nothing.

The platform default (`configs/policies/supply_chain_approval_matrix@1.0.0.
yaml`) ships empty: no action requires approval until a tenant opts in.
Whether a required approval also demands a second approver and a written
reason (separation of duties) is NOT part of this matrix — that stays a
platform-wide floor, `strict_approval_prefixes` at the composition root
(`"supply_chain.case_action."`), not something a tenant can weaken for
themselves; see `.claude/PLAN.md` for the reasoning.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dw_supply_chain.domain.po_case import CaseAction

__all__ = ["SupplyChainApprovalMatrix", "load_supply_chain_approval_matrix"]


class SupplyChainApprovalMatrix(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    approval_required_actions: frozenset[CaseAction] = frozenset()

    @field_validator("approval_required_actions")
    @classmethod
    def _only_steps_the_approval_graph_applies(
        cls, actions: frozenset[CaseAction]
    ) -> frozenset[CaseAction]:
        # `create_po` is its own command (`CreatePO`), never started as an
        # approval run, so gating it here would be read by nothing
        # (failure-modes #1): refuse it rather than look like a safeguard.
        if CaseAction.CREATE_PO in actions:
            raise ValueError("create_po is not gated by the approval matrix")
        return actions

    def requires_approval(self, action: CaseAction) -> bool:
        return action in self.approval_required_actions


def load_supply_chain_approval_matrix(path: Path) -> SupplyChainApprovalMatrix:
    return SupplyChainApprovalMatrix.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
