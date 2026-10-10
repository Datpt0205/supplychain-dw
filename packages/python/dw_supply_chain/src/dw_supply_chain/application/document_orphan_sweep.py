"""The orphan sweep: case-document objects no row holds (ADR 0021, failure-modes #6).

An upload writes the object first and the row second, and deletes the object
when the row fails. A process killed between the two leaves bytes no row
points at; nothing else would ever remove them. The worker's
`supply_chain_document_orphans` lane runs this on the retention cadence.

One run reads one bounded page of keys under `supply_chain/`, carrying on
from where the previous run stopped and starting over after the last page.
For each object it deletes, ALL of these hold:

- it is over a day old (an upload in flight is seconds old);
- its key parses exactly (`ObjectKey.parse`): a key this context did not
  write is logged and left alone, never deleted on a guess;
- asked under the tenant and workspace the key itself names, through the
  ordinary tenant session, the database holds no row with that key. No
  cross-tenant read and no `app.workspace_scope`: the question is asked in
  exactly the scope a row for that key would have to live in, which the
  table's object-key CHECK guarantees.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from dw_kernel.ports import UtcClock
from dw_supply_chain.application.follow_up_sweep import sweep_context
from dw_supply_chain.application.ports import (
    CaseDocumentKeysPort,
    CaseDocumentObjectListingPort,
)
from dw_supply_chain.domain.case_document import ObjectKey

logger = logging.getLogger(__name__)

# Older than any upload could take between its put and its insert.
ORPHAN_AGE = timedelta(days=1)
DEFAULT_BATCH = 500


@dataclass
class SweepOrphanDocuments:
    """Satisfies the worker's `RetentionPrunePort`."""

    objects: CaseDocumentObjectListingPort
    keys: CaseDocumentKeysPort
    clock: UtcClock
    batch_size: int = DEFAULT_BATCH
    # Where the next run starts listing. In memory: a restart starts over,
    # which costs one re-read of keys already seen and nothing else.
    _cursor: str | None = field(default=None, init=False)

    async def prune(self) -> None:
        listed = await self.objects.list_after(
            ObjectKey.ROOT_PREFIX, start_after=self._cursor, limit=self.batch_size
        )
        self._cursor = listed[-1].key if len(listed) == self.batch_size else None
        cutoff = self.clock.now() - ORPHAN_AGE

        by_scope: dict[tuple[uuid.UUID, uuid.UUID], list[str]] = {}
        for stored in listed:
            if stored.last_modified >= cutoff:
                continue
            parsed = ObjectKey.parse(stored.key)
            if parsed is None:
                logger.warning("orphan sweep: skipped a key it cannot read: %r", stored.key)
                continue
            by_scope.setdefault((parsed.tenant_id, parsed.workspace_id), []).append(stored.key)

        deleted = 0
        for (tenant_id, workspace_id), keys in by_scope.items():
            try:
                held = await self.keys.existing_keys(sweep_context(tenant_id, workspace_id), keys)
                for key in keys:
                    if key not in held:
                        await self.objects.delete(key)
                        deleted += 1
            except Exception:
                # One tenant's failure is retried next run; the others proceed.
                logger.exception("orphan sweep failed for tenant %s", tenant_id)
        if deleted:
            logger.info("orphan sweep: deleted %d case-document object(s) with no row", deleted)
