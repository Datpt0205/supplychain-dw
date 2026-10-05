"""SQLAlchemy Core table definitions for the supply_chain schema.

Query-building only — the migration is schema authority, so no CHECK
constraint or trigger is repeated here; this mirrors `dw_platform.adapters.
persistence.tables`'s own convention for the same reason.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dw_kernel.naming import NAMING_CONVENTION

metadata = sa.MetaData(schema="supply_chain", naming_convention=NAMING_CONVENTION)

po_cases = sa.Table(
    "po_cases",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_reference", sa.Text, nullable=False),
    sa.Column("supplier_name", sa.Text, nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="po_created"),
    sa.Column("interrupted_state", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"),
)

supplier_updates = sa.Table(
    "supplier_updates",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), sa.ForeignKey("po_cases.id"), nullable=False),
    sa.Column("raw_text", sa.Text, nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("affected_po", sa.Text, nullable=True),
    sa.Column("delay_days", sa.Integer, nullable=True),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("proposed_action", sa.Text, nullable=False),
    sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
    sa.Column("source_ref", sa.Text, nullable=False),
    sa.Column("requires_confirmation", sa.Boolean, nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

po_case_state_transitions = sa.Table(
    "po_case_state_transitions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), sa.ForeignKey("po_cases.id"), nullable=False),
    sa.Column("from_state", sa.Text, nullable=False),
    sa.Column("to_state", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column(
        "occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

delay_impact_analyses = sa.Table(
    "delay_impact_analyses",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), sa.ForeignKey("po_cases.id"), nullable=False),
    sa.Column(
        "supplier_update_id",
        UUID(as_uuid=True),
        sa.ForeignKey("supplier_updates.id"),
        nullable=False,
    ),
    sa.Column("delay_days", sa.Integer, nullable=False),
    sa.Column("impacted_milestones", JSONB, nullable=False),
    sa.Column("assumptions", JSONB, nullable=False),
    sa.Column("mitigation_options", JSONB, nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

follow_ups = sa.Table(
    "follow_ups",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), sa.ForeignKey("po_cases.id"), nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("episode", sa.Text, nullable=False),
    sa.Column("milestone", sa.Text, nullable=True),
    sa.Column("days", sa.Integer, nullable=False),
    sa.Column("limit_days", sa.Integer, nullable=True),
    sa.Column("recipient_scopes", JSONB, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="open"),
    sa.Column(
        "opened_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("closed_by", UUID(as_uuid=True), nullable=True),
    sa.Column("close_note", sa.Text, nullable=True),
)

case_documents = sa.Table(
    "case_documents",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("doc_type", sa.Text, nullable=False),
    sa.Column("object_key", sa.Text, nullable=False),
    sa.Column("filename", sa.Text, nullable=False),
    sa.Column("content_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.BigInteger, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "uploaded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)
