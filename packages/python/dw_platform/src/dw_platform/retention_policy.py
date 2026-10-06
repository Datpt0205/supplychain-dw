"""How long this deployment keeps data — the contract, not the sweeps.

The rules live here rather than beside any one sweep because "how long do you
keep our data" is one question with one answer. `memory` expires items,
`knowledge` expires deleted documents, and if each owned its own copy of the
schedule the two would drift and the release manifest would carry two versions
of one commitment. Both packages already depend on this one; the kernel, which
is the only layer below it, is deliberately dependency-free and stays that way.

The sweeps themselves stay where the tables are: `dw_memory.retention` and
`dw_knowledge.retention`. This module knows no SQL.

**Deleting, not closing.** `valid_until` answers "this stopped being true" — it
is what supersession writes, and the row stays so a past decision can still be
explained. Retention answers a different question: "we are no longer allowed to
hold this." The two must not share a mechanism, because the second one is what
goes in a contract.

**A class with no `days` never expires.** A legal hold is that shape, deliberately:
an obligation to keep can outlive the ordinary schedule, and the safe way to
express it is a class the sweep cannot touch rather than a flag the sweep must
remember to honour. (`legal_hold` left the policy in 1.5.0 because nothing could
assign it; it returns with the route that sets one.)

**An unknown class is kept, not guessed.** A row naming a class this build does
not have is more likely a newer config than a mistake, and deleting on that
guess is irreversible. It is kept and counted, so the mismatch shows up as a
number rather than as missing data.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AuditRetention",
    "CheckpointRetention",
    "KnowledgeRetention",
    "RetentionClass",
    "RetentionPolicy",
    "load_retention_policy",
]


class RetentionClass(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # `None` means never swept. Not zero, and not absent — an explicit null, so
    # "keep forever" is something somebody wrote rather than something omitted.
    days: int | None = Field(default=None, ge=1)
    description: str


class KnowledgeRetention(BaseModel):
    """Two windows, because two different things expire on the knowledge side.

    `deleted_grace_days` is how long a document survives somebody marking it
    deleted — marking is a user action and users change their minds.

    `orphan_evidence_grace_days` is how long an evidence row survives losing its
    last citation. It exists because `evidence -> documents` is RESTRICT: until
    orphaned evidence goes, a document that was ever cited can never be hard
    deleted, and the document window above would be a promise the schema cannot
    keep. Today evidence and its `item_evidence` link are written in one
    transaction, so an unlinked row is genuinely unlinked; the window is what
    keeps that from being load-bearing for a writer that later splits them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted_grace_days: int = Field(ge=1)
    orphan_evidence_grace_days: int = Field(ge=1)


class AuditRetention(BaseModel):
    """The two range-partitioned tables, where retention is DROP PARTITION.

    A term here destroys a whole month at once and nothing stands between the
    sweep and the data — no soft delete, no grace. So `days` defaults to nothing
    for both tables: the maintenance pass still creates partitions ahead, which
    is pure gain, and drops only what somebody wrote a number for. Same shape as
    `legal_hold` above, for the same reason.

    `months_ahead` has to exceed the gap between two maintenance passes with room
    to spare. A month with no partition sends its rows to the DEFAULT one, and a
    default holding a month's rows blocks that month's partition from ever being
    created — so falling behind is not self-correcting.

    `enforced` is separate from `days` because "nobody has decided a term" and
    "a term is decided but may not run yet" are different states, and collapsing
    them loses the decision. Creating partitions ahead happens either way; only
    dropping is gated. It exists so a decided schedule can be recorded before the
    restore procedure it depends on has been rehearsed — enabling an irreversible
    delete on top of an untested safety net is the wrong order, not a detail.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    months_ahead: int = Field(ge=1, le=24)
    enforced: bool
    tables: dict[str, RetentionClass]

    def cutoff_for(self, table: str, *, now: datetime) -> datetime | None:
        found = self.tables.get(table)
        if not self.enforced or found is None or found.days is None:
            return None
        return now - timedelta(days=found.days)


class CheckpointRetention(BaseModel):
    """How long a finished thread's run checkpoints are kept.

    Every checkpoint holds the thread's whole message list, so each superseded
    one is an older verbatim copy of the conversation — including what context
    compaction removed from the live state. Two terms, because two different
    things stop being needed:

    `superseded_days`: a checkpoint that is not its thread's newest. Only the
    newest is needed to continue the thread; the older ones are history.

    `idle_thread_days`: the whole thread, newest included, once nothing has been
    written to it for this long.

    Only threads whose runs have all finished are touched, whatever the terms
    say — a run waiting for a person resumes from these rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    superseded_days: int = Field(ge=1)
    idle_thread_days: int = Field(ge=1)

    @model_validator(mode="after")
    def _idle_is_the_longer_term(self) -> CheckpointRetention:
        # The sweep reads candidates by the shorter term and decides by the
        # longer one; the other way round, a thread would be emptied sooner than
        # its own superseded checkpoints expire.
        if self.idle_thread_days < self.superseded_days:
            raise ValueError("idle_thread_days must be at least superseded_days")
        return self


class RetentionPolicy(BaseModel):
    """The versioned answer to "how long do you keep our data"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    classes: dict[str, RetentionClass]
    knowledge: KnowledgeRetention
    audit: AuditRetention
    checkpoints: CheckpointRetention
    batch_limit: int = Field(gt=0, le=10_000)

    def cutoff_for(self, name: str, *, now: datetime) -> datetime | None:
        """The instant before which rows of this class have outlived their term.

        `None` for a class that never expires and for one this build does not
        know — the caller keeps the row either way, and only the second is worth
        a log line.
        """
        found = self.classes.get(name)
        if found is None or found.days is None:
            return None
        return now - timedelta(days=found.days)


def load_retention_policy(path: Path) -> RetentionPolicy:
    return RetentionPolicy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
