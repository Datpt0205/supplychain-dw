"""Unit: the support scope catalog and the one reading of "is this grant in force"."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.errors import ConfigError
from dw_platform.application.access_context import AccessContext
from dw_platform.application.support_access import (
    EXPIRED,
    INEFFECTIVE,
    SUPPORT_GRANT_SCOPE,
    GrantStatus,
    SupportGrant,
    SupportScopeCatalog,
    grant_effective_state,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)


class _Describer:
    async def describe(
        self, context: AccessContext, resource_type: str, resource_id: uuid.UUID
    ) -> str | None:
        return "a case"


@pytest.mark.parametrize(
    "scope", ["approvals.decide", "runs.read", "platform.admin", "audit.events", "support.grant"]
)
def test_the_catalog_refuses_a_set_carrying_a_platform_scope(scope: str) -> None:
    catalog = SupportScopeCatalog()
    with pytest.raises(ConfigError, match="may never hold"):
        catalog.register("ctx.read", "Read", {"ctx.records.read", scope}, {"workspace"})
    assert catalog.sets() == []


def test_the_catalog_refuses_a_key_twice() -> None:
    catalog = SupportScopeCatalog()
    catalog.register("ctx.read", "Read", {"ctx.records.read"}, {"workspace"})
    with pytest.raises(ConfigError, match="twice"):
        catalog.register("ctx.read", "Read again", {"ctx.records.read"}, {"workspace"})


def test_the_catalog_refuses_registration_once_frozen() -> None:
    catalog = SupportScopeCatalog()
    catalog.freeze()
    with pytest.raises(ConfigError, match="frozen"):
        catalog.register("ctx.read", "Read", {"ctx.records.read"}, {"workspace"})
    with pytest.raises(ConfigError, match="frozen"):
        catalog.register_resource("case", _Describer())


def test_the_catalog_will_not_freeze_with_a_resource_nobody_describes() -> None:
    catalog = SupportScopeCatalog()
    catalog.register("ctx.read", "Read", {"ctx.records.read"}, {"workspace", "case"})
    with pytest.raises(ConfigError, match="nobody describes"):
        catalog.freeze()
    catalog.register_resource("case", _Describer())
    catalog.freeze()
    assert [s.key for s in catalog.sets()] == ["ctx.read"]


def test_an_empty_catalog_freezes() -> None:
    catalog = SupportScopeCatalog()
    catalog.freeze()
    assert catalog.sets() == []


def _grant(status: GrantStatus = GrantStatus.ACTIVE, *, hours: int = 72) -> SupportGrant:
    activated = NOW - timedelta(hours=1)
    return SupportGrant(
        id=uuid.uuid4(),
        code="SG-0001",
        tenant_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        resource_type="workspace",
        resource_id=None,
        resource_label="Main",
        scope_set_key="ctx.read",
        scope_set_label="Read",
        scopes=frozenset({"ctx.records.read"}),
        reason="the parser misread page 3",
        duration_hours=hours,
        status=status,
        requested_by=uuid.uuid4(),
        requested_at=activated,
        granted_by=uuid.uuid4(),
        granted_at=activated,
        staff_user_id=uuid.uuid4() if status is GrantStatus.ACTIVE else None,
        activated_at=activated if status is GrantStatus.ACTIVE else None,
        expires_at=activated + timedelta(hours=hours) if status is GrantStatus.ACTIVE else None,
    )


GRANTOR = frozenset({SUPPORT_GRANT_SCOPE, "ctx.records.read", "ctx.other"})


def test_an_active_grant_is_active_before_it_expires() -> None:
    assert grant_effective_state(_grant(), NOW, GRANTOR) == "active"


def test_an_active_grant_has_expired_exactly_at_expires_at() -> None:
    grant = _grant()
    assert grant.expires_at is not None
    just_before = grant.expires_at - timedelta(microseconds=1)
    assert grant_effective_state(grant, just_before, GRANTOR) == "active"
    assert grant_effective_state(grant, grant.expires_at, GRANTOR) == EXPIRED


def test_a_grant_is_ineffective_once_the_granter_loses_support_grant() -> None:
    assert grant_effective_state(_grant(), NOW, GRANTOR - {SUPPORT_GRANT_SCOPE}) == INEFFECTIVE


def test_a_grant_is_ineffective_once_the_granter_loses_a_stamped_scope() -> None:
    assert grant_effective_state(_grant(), NOW, GRANTOR - {"ctx.records.read"}) == INEFFECTIVE
    assert grant_effective_state(_grant(), NOW, frozenset()) == INEFFECTIVE


@pytest.mark.parametrize(
    "status",
    [
        GrantStatus.PENDING_APPROVAL,
        GrantStatus.PENDING_ASSIGNMENT,
        GrantStatus.REJECTED,
        GrantStatus.REVOKED,
    ],
)
def test_any_other_status_reads_as_stored(status: GrantStatus) -> None:
    assert grant_effective_state(_grant(status), NOW, GRANTOR) == status.value
