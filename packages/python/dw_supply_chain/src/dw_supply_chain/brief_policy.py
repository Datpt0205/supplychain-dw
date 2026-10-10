"""In which order the daily brief reads its signals, per tenant.

Placed at the package's top level rather than under `domain/`, same
reasoning as `sla_policy.py` and `approval_matrix.py`: this is a versioned
artifact's schema and parser, and it touches the filesystem.

A tenant may REORDER the signals, never drop one: `signal_order` must name
every `BriefSignal` exactly once. Ordering is a preference; hiding a signal
(an escalation nobody reads first because it is not there at all) would turn
a preference into a blind spot, so a document that omits one is refused at
write time, the same way `approval_matrix.py` refuses an unknown action.

Signals added after a version (`SIGNALS_ADDED_AFTER`: the four stage-1 groups
in 1.1.0, ticket 08): a tenant override stored at an older version was written
before they existed, so it never placed them. `from_stored` places those
signals, and only those, where the platform default has them — right after
the nearest signal before them in the platform order — and keeps every place
the tenant chose. A document claiming the current version, and every new
override (the PUT validates whole), must name them all.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.daily_brief import BriefSignal

__all__ = [
    "BRIEF_POLICY_ID",
    "SIGNALS_ADDED_AFTER",
    "SupplyChainBriefPolicy",
    "load_supply_chain_brief_policy",
]

# The document's own `policy_id`, and the key its tenant override is stored under.
BRIEF_POLICY_ID = "supply_chain_brief"

_STAGE_ONE = (
    BriefSignal.PRODUCT_SLA_BREACHED,
    BriefSignal.PRODUCT_AWAITING_BOD,
    BriefSignal.PRODUCT_AWAITING_SIGNOFF,
    BriefSignal.SAMPLE_EVALUATED_TODAY,
)
# For each older version a tenant override may be stored at, the signals it
# did not have: such an override cannot have placed them.
SIGNALS_ADDED_AFTER: Mapping[str, frozenset[BriefSignal]] = {
    "1.0.0": frozenset(_STAGE_ONE),
}


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

    @classmethod
    def from_stored(cls, stored: Mapping[str, object], platform_default: Self) -> Self:
        """A tenant's stored override, validated whole. One stored at an older
        version that leaves out a signal added after it gets that signal where
        the platform default puts it; any other gap is refused, as it always
        was."""
        order = stored.get("signal_order")
        added = SIGNALS_ADDED_AFTER.get(str(stored.get("policy_version")))
        if added is None or not isinstance(order, list | tuple):
            return cls.model_validate(stored)
        placed: list[object] = list(order)
        for signal in platform_default.signal_order:
            if signal not in added or signal.value in placed:
                continue
            before = platform_default.signal_order[: platform_default.signal_order.index(signal)]
            anchor = next(
                (placed.index(s.value) for s in reversed(before) if s.value in placed), -1
            )
            placed.insert(anchor + 1, signal.value)
        return cls.model_validate({**stored, "signal_order": placed})


def load_supply_chain_brief_policy(path: Path) -> SupplyChainBriefPolicy:
    return SupplyChainBriefPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
