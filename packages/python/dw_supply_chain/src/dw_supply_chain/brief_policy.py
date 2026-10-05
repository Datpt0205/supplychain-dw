"""In which order the daily brief reads its signals, per tenant.

Placed at the package's top level rather than under `domain/`, same
reasoning as `sla_policy.py` and `approval_matrix.py`: this is a versioned
artifact's schema and parser, and it touches the filesystem.

A tenant may REORDER the signals, never drop one: `signal_order` must name
every `BriefSignal` exactly once. Ordering is a preference; hiding a signal
(an escalation nobody reads first because it is not there at all) would turn
a preference into a blind spot, so a document that omits one is refused at
write time, the same way `approval_matrix.py` refuses an unknown action.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.daily_brief import BriefSignal

__all__ = ["SupplyChainBriefPolicy", "load_supply_chain_brief_policy"]


class SupplyChainBriefPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    signal_order: tuple[BriefSignal, ...]

    @model_validator(mode="after")
    def _every_signal_exactly_once(self) -> SupplyChainBriefPolicy:
        listed = list(self.signal_order)
        missing = [signal.value for signal in BriefSignal if signal not in listed]
        repeated = sorted({signal.value for signal in listed if listed.count(signal) > 1})
        if missing or repeated:
            raise ValueError(
                "signal_order must list every brief signal exactly once"
                f" (missing: {missing or 'none'}; repeated: {repeated or 'none'})"
            )
        return self


def load_supply_chain_brief_policy(path: Path) -> SupplyChainBriefPolicy:
    return SupplyChainBriefPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
