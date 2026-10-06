"""memory fixed-set checks

Revision ID: a4104b95722b
Revises: 855ae928c3fa
Create Date: 2026-10-06

`memory.items` and `memory.write_candidates` keep five columns whose values
come from a fixed set, and the database accepted any text in all of them
(`failure-modes.md` #7). The service only ever writes enum values, but the
service is not what a second writer, a repair script or a bug goes through, and
one of these is load-bearing: recall's clearance filter reads `classification`,
so a row saying `public` is a fact no clearance recalls — or, once something
starts ranking unknown values, one recalled by a guess.

- `memory_type` (both tables): `dw_memory.contracts.MemoryType`.
- `classification` (both tables): the clearance ladder,
  `dw_knowledge.contracts.CLASSIFICATIONS`.
- `decision` (`write_candidates`): `dw_memory.contracts.WriteDecision`.

Each list is a second copy of a set the code owns, written out here because a
migration is history and must not change when the code does. What keeps the
copies honest is `test_the_checks_hold_exactly_the_sets_the_code_owns`, which
reads these constraints back from `pg_constraint` and compares them with the
enums — a value added on one side only goes red.

Not `retention_policy`: its set belongs to the versioned retention file, and a
row naming a class that file does not have is already kept and counted rather
than refused. A CHECK would be a second owner of that set.

Added validated, not `NOT VALID`: a row already off the set should stop the
migration and be looked at, not be grandfathered in silently. Measured on this
repository's dev database before writing it: both tables were empty, which is
expected while nothing in production proposes memories yet.
"""

from __future__ import annotations

from alembic import op

revision = "a4104b95722b"
down_revision = "855ae928c3fa"
branch_labels = None
depends_on = None

_MEMORY_TYPES = ("episodic", "semantic", "procedural", "preference", "commitment")
_CLASSIFICATIONS = ("internal", "confidential", "restricted")
_DECISIONS = ("auto_write", "review", "reject")

# (table, column, allowed). Names follow NAMING_CONVENTION: ck_<table>_<column>.
_CHECKS = (
    ("items", "memory_type", _MEMORY_TYPES),
    ("items", "classification", _CLASSIFICATIONS),
    ("write_candidates", "memory_type", _MEMORY_TYPES),
    ("write_candidates", "classification", _CLASSIFICATIONS),
    ("write_candidates", "decision", _DECISIONS),
)


def upgrade() -> None:
    for table, column, allowed in _CHECKS:
        values = ", ".join(f"'{value}'" for value in allowed)
        op.execute(
            f"ALTER TABLE memory.{table} ADD CONSTRAINT ck_{table}_{column}"
            f" CHECK ({column} IN ({values}))"
        )


def downgrade() -> None:
    for table, column, _ in reversed(_CHECKS):
        op.execute(f"ALTER TABLE memory.{table} DROP CONSTRAINT ck_{table}_{column}")
