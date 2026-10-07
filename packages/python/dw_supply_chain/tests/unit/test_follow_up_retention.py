"""Unit: how long a closed follow-up is kept, and the lane that prunes it
(ticket P3).

The term is a field of the follow-up policy (1.2): the platform document's
value is the default and the floor, a tenant may keep longer and never
shorter. The lane visits each workspace with cases under the sweep's own
context, resolves that tenant's term, and asks the database to prune; what
may be deleted is decided in the database (`test_follow_ups_retention.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dw_kernel.errors import DomainError
from dw_kernel.ports import FixedClock, Uuid4Generator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.authorization import ScopeAuthorizationService
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.follow_up_retention import PruneClosedFollowUps
from dw_supply_chain.application.follow_up_sweep import SWEEP_PRINCIPAL
from dw_supply_chain.application.handlers import FOLLOW_UP_POLICY_ID, SetFollowUpPolicyOverride
from dw_supply_chain.follow_up_policy import (
    SupplyChainFollowUpPolicy,
    follow_up_retention_days,
    load_supply_chain_follow_up_policy,
)
from dw_supply_chain.policy_files import FOLLOW_UP_POLICY_FILE

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SHIPPED = load_supply_chain_follow_up_policy(
    REPO_ROOT / "configs" / "policies" / FOLLOW_UP_POLICY_FILE
)
_RECIPIENTS = {
    "update_reminder": ["supply_chain.supplier_update.write"],
    "update_escalation": ["supply_chain.supplier_update.write"],
    "sla_breach": ["supply_chain.supplier_update.write"],
}


def _policy(schema_version: str = "1.2", **extra: object) -> SupplyChainFollowUpPolicy:
    return SupplyChainFollowUpPolicy.model_validate(
        {
            "schema_version": schema_version,
            "policy_id": FOLLOW_UP_POLICY_ID,
            "policy_version": "1.2.0",
            "recipients": _RECIPIENTS,
            **extra,
        }
    )


def test_the_shipped_policy_keeps_closed_follow_ups_180_days() -> None:
    assert SHIPPED.schema_version == "1.2"
    assert SHIPPED.closed_retention_days == 180
    assert follow_up_retention_days(SHIPPED, SHIPPED) == 180


def test_a_1_2_policy_must_name_its_term() -> None:
    with pytest.raises(ValidationError, match="closed_retention_days"):
        _policy()


@pytest.mark.parametrize("schema_version", ["1.0", "1.1"])
def test_a_term_is_refused_before_1_2(schema_version: str) -> None:
    with pytest.raises(ValidationError, match="closed_retention_days"):
        _policy(schema_version, closed_retention_days=200)


@pytest.mark.parametrize("days", [0, -1, 3651])
def test_a_term_outside_one_day_to_ten_years_is_refused(days: int) -> None:
    with pytest.raises(ValidationError):
        _policy(closed_retention_days=days)


@pytest.mark.parametrize(
    ("tenant", "expected"),
    [
        pytest.param(_policy("1.1"), 180, id="override-without-a-term-keeps-the-platforms"),
        pytest.param(_policy(closed_retention_days=400), 400, id="longer-is-the-tenants"),
        pytest.param(_policy(closed_retention_days=30), 180, id="shorter-is-the-floor"),
    ],
)
def test_the_effective_term_never_goes_below_the_platforms(
    tenant: SupplyChainFollowUpPolicy, expected: int
) -> None:
    assert follow_up_retention_days(tenant, SHIPPED) == expected


def test_a_platform_document_without_a_term_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="retention term"):
        follow_up_retention_days(SHIPPED, _policy("1.1"))


# -- SetFollowUpPolicyOverride ---------------------------------------------------


@dataclass
class _Overrides:
    stored: dict[tuple[uuid.UUID, str], dict[str, object]] = field(default_factory=dict)
    audits: list[AuditEvent] = field(default_factory=list)

    async def get(self, context: AccessContext, policy_id: str) -> dict[str, object] | None:
        return self.stored.get((context.tenant_id, policy_id))

    async def put(
        self,
        context: AccessContext,
        policy_id: str,
        content: Mapping[str, object],
        *,
        audit: AuditEvent,
    ) -> None:
        self.stored[(context.tenant_id, policy_id)] = dict(content)
        self.audits.append(audit)


def _admin(tenant: uuid.UUID | None = None) -> AccessContext:
    return AccessContext(
        tenant_id=tenant or uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        principal_id=uuid.uuid4(),
        roles=frozenset({"sc_process_admin"}),
        scopes=frozenset({"supply_chain.follow_up_policy.write"}),
        plan_id="professional",
    )


def _set(overrides: _Overrides) -> SetFollowUpPolicyOverride:
    return SetFollowUpPolicyOverride(
        policy_override_repo=overrides,
        platform_default_policy=SHIPPED,
        authz=ScopeAuthorizationService(),
        ids=Uuid4Generator(),
        clock=FixedClock(datetime(2026, 10, 7, tzinfo=UTC)),
    )


async def test_an_override_shorter_than_the_platform_term_is_refused() -> None:
    overrides = _Overrides()

    with pytest.raises(DomainError, match="180"):
        await _set(overrides).handle(_admin(), _policy(closed_retention_days=179))

    assert overrides.stored == {} and overrides.audits == []


async def test_an_override_at_or_above_the_platform_term_is_kept() -> None:
    overrides = _Overrides()
    admin = _admin()

    await _set(overrides).handle(admin, _policy(closed_retention_days=180))

    assert overrides.stored[(admin.tenant_id, FOLLOW_UP_POLICY_ID)]["closed_retention_days"] == 180


# -- PruneClosedFollowUps --------------------------------------------------------


@dataclass
class _Workspaces:
    pairs: list[tuple[uuid.UUID, uuid.UUID]]

    async def workspaces(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        return self.pairs


@dataclass
class _Prune:
    calls: list[tuple[AccessContext, int]] = field(default_factory=list)
    failing: frozenset[uuid.UUID] = frozenset()

    async def prune_closed(self, context: AccessContext, *, older_than_days: int) -> int:
        if context.workspace_id in self.failing:
            raise RuntimeError("database went away")
        self.calls.append((context, older_than_days))
        return 0


def _lane(
    pairs: list[tuple[uuid.UUID, uuid.UUID]], overrides: _Overrides, prune: _Prune
) -> PruneClosedFollowUps:
    return PruneClosedFollowUps(
        workspaces=_Workspaces(pairs),
        policy_override_repo=overrides,
        platform_default_policy=SHIPPED,
        follow_ups=prune,
    )


async def test_each_workspace_is_pruned_under_its_own_tenants_term() -> None:
    a, a_ws1, a_ws2, b, b_ws = (uuid.uuid4() for _ in range(5))
    overrides = _Overrides()
    overrides.stored[(b, FOLLOW_UP_POLICY_ID)] = _policy(closed_retention_days=400).model_dump(
        mode="json"
    )
    prune = _Prune()

    await _lane([(a, a_ws1), (a, a_ws2), (b, b_ws)], overrides, prune).prune()

    seen: list[tuple[Any, ...]] = [
        (c.tenant_id, c.workspace_id, c.principal_id, c.scopes, days) for c, days in prune.calls
    ]
    assert seen == [
        (a, a_ws1, SWEEP_PRINCIPAL, frozenset(), 180),
        (a, a_ws2, SWEEP_PRINCIPAL, frozenset(), 180),
        (b, b_ws, SWEEP_PRINCIPAL, frozenset(), 400),
    ]


async def test_one_workspace_failing_leaves_the_others_pruned() -> None:
    a, a_ws, b, b_ws = (uuid.uuid4() for _ in range(4))
    prune = _Prune(failing=frozenset({a_ws}))

    await _lane([(a, a_ws), (b, b_ws)], _Overrides(), prune).prune()

    assert [(c.tenant_id, c.workspace_id) for c, _ in prune.calls] == [(b, b_ws)]
