"""Unit: the orphan sweep for case-document objects, against fakes.

An object is an orphan when no document row holds its key: the upload wrote
the bytes and died before the row. The sweep deletes one only when it is over
a day old (an upload in flight is younger), its key parses exactly, and the
database, asked under the tenant and workspace the key names, holds no row.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from dw_kernel.errors import InfrastructureError
from dw_kernel.ports import FixedClock
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.document_orphan_sweep import SweepOrphanDocuments
from dw_supply_chain.application.ports import StoredObject
from dw_supply_chain.domain.case_document import CaseKind, ObjectKey

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=1, minutes=1)
FRESH = NOW - timedelta(hours=23)


def _key(tenant: uuid.UUID | None = None, workspace: uuid.UUID | None = None) -> str:
    return ObjectKey.build(
        tenant_id=tenant or uuid.uuid4(),
        workspace_id=workspace or uuid.uuid4(),
        case_kind=CaseKind.PO,
        case_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
    ).value


@dataclass
class FakeBucket:
    objects: dict[str, datetime] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    listings: list[tuple[str, str | None, int]] = field(default_factory=list)

    async def list_after(
        self, prefix: str, *, start_after: str | None, limit: int
    ) -> list[StoredObject]:
        self.listings.append((prefix, start_after, limit))
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        if start_after is not None:
            keys = [k for k in keys if k > start_after]
        return [StoredObject(key=k, last_modified=self.objects[k]) for k in keys[:limit]]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


@dataclass
class FakeRows:
    """Answers like the real query under RLS: only rows of the context's own
    tenant and workspace are visible."""

    rows: set[tuple[uuid.UUID, uuid.UUID, str]] = field(default_factory=set)
    asked: list[tuple[uuid.UUID, uuid.UUID, tuple[str, ...]]] = field(default_factory=list)
    fail_for: set[uuid.UUID] = field(default_factory=set)

    async def existing_keys(self, context: AccessContext, keys: Sequence[str]) -> set[str]:
        self.asked.append((context.tenant_id, context.workspace_id, tuple(keys)))
        if context.tenant_id in self.fail_for:
            raise InfrastructureError("database went away")
        return {
            k for (t, w, k) in self.rows if (t, w) == (context.tenant_id, context.workspace_id)
        } & set(keys)

    def hold(self, key: str) -> None:
        parsed = ObjectKey.parse(key)
        assert parsed is not None
        self.rows.add((parsed.tenant_id, parsed.workspace_id, key))


def _sweep(bucket: FakeBucket, rows: FakeRows, batch: int = 100) -> SweepOrphanDocuments:
    return SweepOrphanDocuments(objects=bucket, keys=rows, clock=FixedClock(NOW), batch_size=batch)


async def test_an_old_object_without_a_row_is_deleted() -> None:
    orphan = _key()
    bucket, rows = FakeBucket({orphan: OLD}), FakeRows()

    await _sweep(bucket, rows).prune()

    assert bucket.deleted == [orphan]


async def test_an_object_with_its_row_is_kept() -> None:
    held = _key()
    bucket, rows = FakeBucket({held: OLD}), FakeRows()
    rows.hold(held)

    await _sweep(bucket, rows).prune()

    assert bucket.deleted == []


async def test_a_fresh_object_without_a_row_is_kept() -> None:
    """An upload between its put and its insert looks exactly like this."""
    bucket, rows = FakeBucket({_key(): FRESH}), FakeRows()

    await _sweep(bucket, rows).prune()

    assert bucket.deleted == []
    assert rows.asked == [], "nothing old enough, so nothing to ask"


@pytest.mark.parametrize(
    "malformed",
    [
        f"supply_chain/{uuid.uuid4()}/not-a-uuid/po/{uuid.uuid4()}/{uuid.uuid4()}",
        f"supply_chain/{uuid.uuid4()}/{uuid.uuid4()}/po/{uuid.uuid4()}",
        "supply_chain/stray.pdf",
    ],
)
async def test_a_key_it_cannot_read_is_skipped_and_logged_never_deleted(
    malformed: str, caplog: pytest.LogCaptureFixture
) -> None:
    bucket, rows = FakeBucket({malformed: OLD}), FakeRows()

    with caplog.at_level(logging.WARNING):
        await _sweep(bucket, rows).prune()

    assert bucket.deleted == []
    assert malformed in caplog.text


async def test_each_key_is_asked_about_under_its_own_tenant_and_workspace() -> None:
    a_tenant, a_ws, b_tenant, b_ws = (uuid.uuid4() for _ in range(4))
    a_key, b_key = _key(a_tenant, a_ws), _key(b_tenant, b_ws)
    bucket, rows = FakeBucket({a_key: OLD, b_key: OLD}), FakeRows()
    # A row in tenant B that names A's key must not save A's object.
    rows.rows.add((b_tenant, b_ws, a_key))
    rows.hold(b_key)

    await _sweep(bucket, rows).prune()

    assert {(t, w) for t, w, _ in rows.asked} == {(a_tenant, a_ws), (b_tenant, b_ws)}
    assert bucket.deleted == [a_key]


async def test_a_failing_tenant_does_not_stop_the_others() -> None:
    failing, healthy = uuid.uuid4(), uuid.uuid4()
    lost, orphan = _key(failing), _key(healthy)
    bucket, rows = FakeBucket({lost: OLD, orphan: OLD}), FakeRows(fail_for={failing})

    await _sweep(bucket, rows).prune()

    assert bucket.deleted == [orphan]


async def test_one_run_reads_one_bounded_batch_and_the_next_carries_on() -> None:
    keys = sorted(_key() for _ in range(5))
    bucket, rows = FakeBucket(dict.fromkeys(keys, OLD)), FakeRows()
    for key in keys:
        rows.hold(key)
    sweep = _sweep(bucket, rows, batch=2)

    for _ in range(4):
        await sweep.prune()

    assert [(p, after, limit) for p, after, limit in bucket.listings] == [
        ("supply_chain/", None, 2),
        ("supply_chain/", keys[1], 2),
        ("supply_chain/", keys[3], 2),
        # The last page was short, so the listing starts over.
        ("supply_chain/", None, 2),
    ]
