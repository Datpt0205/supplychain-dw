"""SQLAlchemy Core tables for the memory schema (mirrors migration 0002)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dw_kernel.naming import NAMING_CONVENTION

metadata = sa.MetaData(schema="memory", naming_convention=NAMING_CONVENTION)

items = sa.Table(
    "items",
    metadata,
    sa.Column("memory_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("worker_id", sa.Text, nullable=False),
    sa.Column("memory_type", sa.Text, nullable=False),
    sa.Column("subject_refs", JSONB, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("structured_facts", JSONB, nullable=False),
    sa.Column("provenance_refs", JSONB, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("valid_from", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("valid_until", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("retention_policy", sa.Text, nullable=False),
    sa.Column("memory_schema_version", sa.Text, nullable=False),
    sa.Column("created_by_run_id", UUID(as_uuid=True), nullable=False),
    sa.Column("fact_key", sa.Text, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

write_candidates = sa.Table(
    "write_candidates",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("worker_id", sa.Text, nullable=False),
    sa.Column("memory_type", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("structured_facts", JSONB, nullable=False),
    sa.Column("provenance_refs", JSONB, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("decision", sa.Text, nullable=False),
    sa.Column("memory_id", UUID(as_uuid=True), nullable=True),
    sa.Column("created_by_run_id", UUID(as_uuid=True), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    # Revision 5e6ccac63d45: what a reviewed candidate needs to become the item
    # it describes — recall matches on the subjects, supersession on the key.
    sa.Column("subject_refs", JSONB, nullable=False),
    sa.Column("fact_key", sa.Text, nullable=True),
)

# Migration 0007. Which memory rests on which evidence, with real foreign keys in
# both directions. `items.provenance_refs` keeps the full JSONB copy of what the
# model was shown; this is what makes those references resolvable, and both are
# written in the same transaction.
item_evidence = sa.Table(
    "item_evidence",
    metadata,
    sa.Column("memory_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("evidence_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
)
