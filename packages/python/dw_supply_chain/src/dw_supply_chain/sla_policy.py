"""Supply Chain SLA reference values — the strategy doc's SLA table, as data.

Placed at the package's top level rather than under `domain/`, same as
`dw_platform.retention_policy`: this is a versioned artifact's schema and
parser, not a business rule about a `POCase`, and it touches the filesystem
(`load_supply_chain_sla_policy` reads a YAML file), which domain code must
not do.

The shipped durations are short test values Đạt asked for (1.1.0), marked
`confirmed` so evaluation runs; a tenant sets its own through its override.
`duration_days_for` returns `None` for a pending milestone and for an unknown
one alike, on purpose: nothing that calls it can accidentally treat an
unconfirmed reference number as a real SLA threshold to enforce or alert
on.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.missing_update import UpdateCadence

__all__ = [
    "SLAConfirmationStatus",
    "SLAMilestone",
    "SupplierUpdateCadence",
    "SupplyChainSLAPolicy",
    "load_supply_chain_sla_policy",
]

_DURATION_PATTERN = re.compile(r"^(\d+)d$")


def _days(duration: str) -> int:
    match = _DURATION_PATTERN.match(duration)
    assert match is not None  # guaranteed by each field's own pattern
    return int(match.group(1))


class SLAConfirmationStatus(StrEnum):
    """Whether a milestone's duration is real policy yet.

    `PENDING_BUSINESS_CONFIRMATION` marks a number nobody has confirmed,
    such as Elmich's reference values, which 1.0.0 shipped that way.
    `CONFIRMED` is the only status a caller may treat as an enforceable
    threshold. The shipped 1.1.0 test values set it on Đạt's instruction; a
    customer's real numbers are that tenant's own override.
    """

    PENDING_BUSINESS_CONFIRMATION = "pending_business_confirmation"
    CONFIRMED = "confirmed"


class SLAMilestone(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Whole days only, matching every example the source document gives
    # (4d/5d/10d/21d) — no other unit is in scope until one is actually seen.
    duration: str = Field(pattern=r"^\d+d$")
    status: SLAConfirmationStatus
    description: str = ""

    @property
    def duration_days(self) -> int:
        return _days(self.duration)

    def is_confirmed(self) -> bool:
        return self.status is SLAConfirmationStatus.CONFIRMED


class SupplierUpdateCadence(BaseModel):
    """How long a case may go without a supplier update: a reminder is due
    after `reminder_after`, an escalation after `escalation_after`. "0d"
    makes a reminder due at once, which is how a tenant tests the chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reminder_after: str = Field(pattern=r"^\d+d$")
    escalation_after: str = Field(pattern=r"^\d+d$")

    @model_validator(mode="after")
    def _escalation_comes_after_the_reminder(self) -> SupplierUpdateCadence:
        if _days(self.escalation_after) <= _days(self.reminder_after):
            raise ValueError("escalation_after must be longer than reminder_after")
        return self

    @property
    def cadence(self) -> UpdateCadence:
        return UpdateCadence(
            reminder_after_days=_days(self.reminder_after),
            escalation_after_days=_days(self.escalation_after),
        )


class SupplyChainSLAPolicy(BaseModel):
    """The versioned answer to "how long should each PO milestone take".

    `sla` is a dict keyed by milestone name rather than one field per
    milestone — same shape as `RetentionPolicy.classes` in dw_platform: the
    set of milestones is data (today the doc's six names; DW-SC-01 will add
    more once it exists), not a fixed set this code has to be edited to grow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    sla: dict[str, SLAMilestone]
    # Required, with no default here: a default in code would be a second
    # copy of the number beside the shipped file.
    supplier_update: SupplierUpdateCadence

    def duration_days_for(self, milestone: str) -> int | None:
        """The confirmed duration for a milestone, or `None`.

        `None` both for an unknown milestone and for one still
        `pending_business_confirmation` — deliberately the same answer, so a
        caller cannot tell the two apart and accidentally enforce a
        reference number Elmich never signed off on. Only a milestone marked
        `confirmed` ever returns a number.
        """
        found = self.sla.get(milestone)
        if found is None or not found.is_confirmed():
            return None
        return found.duration_days


def load_supply_chain_sla_policy(path: Path) -> SupplyChainSLAPolicy:
    return SupplyChainSLAPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
