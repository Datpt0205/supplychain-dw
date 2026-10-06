"""SQLAlchemy Core tables for the knowledge schema (mirrors migration 0002)."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dw_kernel.naming import NAMING_CONVENTION

metadata = sa.MetaData(schema="knowledge", naming_convention=NAMING_CONVENTION)

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    # What makes this "the same document" on re-ingest (migration 0037). NULL
    # means the title decides, which is what every pre-0037 row relies on.
    sa.Column("identity_key", sa.Text, nullable=True),
    sa.Column("domain", sa.Text, nullable=False),
    sa.Column("source_uri", sa.Text, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("source_version", sa.Text, nullable=False),
    sa.Column("index_version", sa.Text, nullable=True),
    sa.Column("created_by", UUID(as_uuid=True), nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    # Versioning + soft-delete (migration 0008).
    sa.Column("doc_key", UUID(as_uuid=True), nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.text("true")),
    sa.Column("effective_from", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("effective_to", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("deleted_by", UUID(as_uuid=True), nullable=True),
    # Sharing scope (migration 0009): "tenant" (private) | "global" (legal docs
    # readable by every tenant). Global-read is enforced by an RLS SELECT policy.
    sa.Column("scope", sa.Text, nullable=False, server_default="tenant"),
    # Business metadata from the ingesting context. Kept in Postgres as well as
    # in the vector payload so a reindex can rebuild the payload from the system
    # of record rather than from Qdrant.
    sa.Column("extra", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # Who may retrieve this (migration 0039). Here as well as in the vector
    # payload for the same reason as `extra`: a reindex rebuilds points from the
    # system of record, and cannot rebuild a fence it has no copy of.
    sa.Column(
        "acl_principals",
        sa.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{tenant:*}'::text[]"),
    ),
)


def visible_from_workspace(workspace_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """A document this workspace may draw on: its own, or any global one.

    One expression for every SQL reader — listing, full read, and the evidence
    check behind a stored memory — so "may this team use that document" has a
    single answer. RLS still narrows by tenant; this is the narrower fence
    inside it, between two teams of one tenant.
    """
    return sa.or_(documents.c.workspace_id == workspace_id, documents.c.scope == "global")


chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("document_id", UUID(as_uuid=True), nullable=False),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("start_offset", sa.Integer, nullable=False),
    sa.Column("end_offset", sa.Integer, nullable=False),
    sa.Column("provenance_hash", sa.Text, nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    # Soft-delete (migration 0008).
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# Durable async upload queue (migration 0009). The API enqueues on upload; the
# worker drains it to run parse(OCR) → chunk → embed → index off the request path.
ingest_jobs = sa.Table(
    "ingest_jobs",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("created_by", UUID(as_uuid=True), nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("identity_key", sa.Text, nullable=True),
    sa.Column("domain", sa.Text, nullable=False, server_default="shared"),
    sa.Column("classification", sa.Text, nullable=False, server_default="internal"),
    sa.Column("source_version", sa.Text, nullable=False, server_default="1"),
    sa.Column("scope", sa.Text, nullable=False, server_default="tenant"),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("content_type", sa.Text, nullable=False),
    sa.Column("storage_key", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    # Lease + retry (migration 0038). Without a lease a crashed worker strands
    # its row in "processing" for ever; without attempts being read, the first
    # transient provider failure was permanent.
    sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
    sa.Column(
        "available_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    ),
    sa.Column("lease_until", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    # What went wrong with a file that was indexed anyway. `error` means the
    # job failed; this means it succeeded and the result is incomplete.
    sa.Column("warnings", sa.ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("document_id", UUID(as_uuid=True), nullable=True),
    sa.Column("chunk_count", sa.Integer, nullable=True),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
    # Carried from enqueue to the worker so the resulting document keeps the
    # metadata of whatever produced the file.
    sa.Column("extra", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    # Who may retrieve the resulting document. The queue used to have no way to
    # say, so everything the worker ingested became readable tenant-wide.
    sa.Column(
        "acl_principals",
        sa.ARRAY(sa.Text),
        nullable=False,
        server_default=sa.text("'{tenant:*}'::text[]"),
    ),
)


# Migration 0007. The evidence a stored memory actually rests on — not a log of
# everything ever retrieved, which is a different table with a different lifetime.
# A row is written when a memory cites it, and `memory.item_evidence` ties the two
# together with real foreign keys, so `EvidenceRef.evidence_id` resolves to
# something rather than naming a UUID that was never stored anywhere.
evidence = sa.Table(
    "evidence",
    metadata,
    sa.Column("evidence_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("source_document_id", UUID(as_uuid=True), nullable=False),
    sa.Column("chunk_id", UUID(as_uuid=True), nullable=True),
    sa.Column("source_version", sa.Text, nullable=False),
    sa.Column("page", sa.Integer, nullable=True),
    sa.Column("start_offset", sa.Integer, nullable=True),
    sa.Column("end_offset", sa.Integer, nullable=True),
    sa.Column("quote", sa.Text, nullable=True),
    sa.Column("relevance_score", sa.Float, nullable=False),
    sa.Column("classification", sa.Text, nullable=False),
    sa.Column("provenance_hash", sa.Text, nullable=False),
    sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
)
