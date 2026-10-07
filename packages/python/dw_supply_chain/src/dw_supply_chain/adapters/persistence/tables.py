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
    # NULL only while the case awaits its PO (`order_requested`), or cancelled
    # from there (`ck_po_cases_po_reference`, ADR 0017).
    sa.Column("po_reference", sa.Text, nullable=True),
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
    sa.Column("order_kind", sa.Text, nullable=False),
    # Set when ĐẶT HÀNG opened the case, with the product case's PIC and
    # Category stamped beside it (migration 84d1c1946b44).
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=True),
    sa.Column("pic_user_id", UUID(as_uuid=True), nullable=True),
    sa.Column("category", sa.Text, nullable=True),
)

# One planned line of a PO case: a SKU and how many (NULL until `create_po`
# when the SKU's planned quantity was open).
po_case_lines = sa.Table(
    "po_case_lines",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("po_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("sku_id", UUID(as_uuid=True), nullable=False),
    sa.Column("quantity", sa.Integer, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
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
    # Exactly one of the two is set (`ck_case_documents_one_case`).
    sa.Column("po_case_id", UUID(as_uuid=True), nullable=True),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=True),
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

product_dev_cases = sa.Table(
    "product_dev_cases",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("proposal_code", sa.Text, nullable=False),
    sa.Column("product_name", sa.Text, nullable=False),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("supplier_name", sa.Text, nullable=True),
    sa.Column("pic_user_id", UUID(as_uuid=True), nullable=False),
    sa.Column("state", sa.Text, nullable=False, server_default="proposed"),
    sa.Column("interrupted_state", sa.Text, nullable=True),
    sa.Column("sample_round", sa.Integer, nullable=False, server_default="0"),
    # How many times the case was submitted for sign-off (76bd1b5fc546).
    sa.Column("signoff_round", sa.Integer, nullable=False, server_default="0"),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    sa.Column("created_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

product_dev_case_state_transitions = sa.Table(
    "product_dev_case_state_transitions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("from_state", sa.Text, nullable=True),
    sa.Column("to_state", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "occurred_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    # The paper of steps 7 and 8 (3fc6599ecd5e); a round's paper is on the round.
    sa.Column("document_id", UUID(as_uuid=True), nullable=True),
)

product_sample_rounds = sa.Table(
    "product_sample_rounds",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("round_no", sa.Integer, nullable=False),
    sa.Column(
        "opened_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("opened_by", UUID(as_uuid=True), nullable=False),
    sa.Column("result", sa.Text, nullable=True),
    sa.Column("evaluation_document_id", UUID(as_uuid=True), nullable=True),
    sa.Column("closed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("closed_by", UUID(as_uuid=True), nullable=True),
)

sample_revision_requests = sa.Table(
    "sample_revision_requests",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("round_no", sa.Integer, nullable=False),
    sa.Column("revision_document_id", UUID(as_uuid=True), nullable=False),
    sa.Column("requested_changes", sa.Text, nullable=False),
    sa.Column("sent_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "sent_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# Step 9 (76bd1b5fc546): a case's official item code, unique per tenant, and
# its SKUs, each unique per tenant and under the case's own item code.
item_codes = sa.Table(
    "item_codes",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("code", sa.Text, nullable=False),
    sa.Column("issued_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

skus = sa.Table(
    "skus",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("product_dev_case_id", UUID(as_uuid=True), nullable=False),
    sa.Column("item_code_id", UUID(as_uuid=True), nullable=False),
    sa.Column("sku_code", sa.Text, nullable=False),
    sa.Column("variant_label", sa.Text, nullable=False),
    sa.Column("planned_quantity", sa.Integer, nullable=True),
    sa.Column("added_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "added_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# A chat proposal being drafted (zalo-channel ticket 04): one open draft per
# person, workspace and channel; consumed when it becomes a case.
proposal_drafts = sa.Table(
    "proposal_drafts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), nullable=False),
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column("draft", JSONB, nullable=False),
    sa.Column("draft_version", sa.Integer, nullable=False),
    sa.Column("summarized_version", sa.Integer, nullable=True),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)
