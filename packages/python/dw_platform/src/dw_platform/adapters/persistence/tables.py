"""SQLAlchemy Core table definitions for the platform schema.

Imperative (non-ORM) mapping keeps the domain free of SQLAlchemy; repositories
translate rows <-> domain objects explicitly. Mirrors the Alembic migration —
change both together.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from dw_kernel.naming import NAMING_CONVENTION

metadata = sa.MetaData(schema="platform", naming_convention=NAMING_CONVENTION)

tenants = sa.Table(
    "tenants",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("slug", sa.Text, nullable=False, unique=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("record_visibility", sa.Text, nullable=False, server_default="open"),
    # Migration 0006. The ceiling this tenant holds its workers under; see
    # `dw_kernel.autonomy`. A CHECK constraint in the database rejects any other value.
    sa.Column("max_autonomy_level", sa.Text, nullable=False, server_default="A4"),
    sa.Column("timezone", sa.Text, nullable=True),
    sa.Column("locale", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("slug", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.UniqueConstraint("tenant_id", "slug", name="uq_workspaces_tenant_slug"),
)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("subject", sa.Text, nullable=False, unique=True),
    sa.Column("email", sa.Text, nullable=True, unique=True),
    sa.Column("display_name", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

roles = sa.Table(
    "roles",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
)

# Additive scope bundles (ADR-001 Phase 3): a versioned catalog, like roles, that
# an admin attaches to a membership on top of its role. Global config, no RLS.
permission_sets = sa.Table(
    "permission_sets",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
)

# Maps a verified (issuer, subject) — e.g. a Keycloak sub or a dev token subject —
# to a platform user. Identity plane (like users): not tenant-scoped, no RLS.
# One platform user may have several external identities (dev + Keycloak).
external_identities = sa.Table(
    "external_identities",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("issuer", sa.Text, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False, server_default="oidc"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.UniqueConstraint("issuer", "subject", name="uq_external_identities_issuer_subject"),
)

# Providers whose `external_identities` rows are a delivery address (a chat to
# send to), not a login. A row of one of these never resolves a verified token
# to a user: whoever controls that chat proved nothing to an identity provider.
CHANNEL_LINK_PROVIDERS: tuple[str, ...] = ("zalo",)

# One-time nonces behind a channel link token (migration cf66605631d7). Identity
# plane like `external_identities`: keyed by user, no tenant, no RLS — a link
# belongs to the person, not to one of their workspaces (ADR 0012).
channel_link_nonces = sa.Table(
    "channel_link_nonces",
    metadata,
    sa.Column("jti", sa.Text, primary_key=True),
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column(
        "user_id",
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# One row per inbound chat message id, claimed before the message is acted on
# (migration 988592a8100f). Identity plane like the nonces above: the dedupe runs
# before a tenant is known, so there is no tenant to narrow by.
channel_inbound_messages = sa.Table(
    "channel_inbound_messages",
    metadata,
    sa.Column("channel", sa.Text, primary_key=True),
    sa.Column("external_message_id", sa.Text, primary_key=True),
    sa.Column(
        "user_id",
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    sa.Column(
        "received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("outcome", sa.Text, nullable=False, server_default="processing"),
)

plans = sa.Table(
    "plans",
    metadata,
    sa.Column("plan_id", sa.Text, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("features", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("quotas", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
)

memberships = sa.Table(
    "memberships",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("role_keys", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("permission_set_keys", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column("clearance", sa.Text, nullable=False, server_default="internal"),
    sa.Column("department", sa.Text, nullable=False, server_default="general"),
    sa.Column(
        "manager_user_id",
        UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.UniqueConstraint("tenant_id", "workspace_id", "user_id", name="uq_memberships_scope_user"),
)

# The workspace a person chose for their chat commands (migration 988592a8100f).
# RLS by `app.principal_id`, not by tenant: the row is the person's, and the bot
# reads it before a tenant is known. The FK to the membership keeps it naming a
# workspace the person belongs to, and removes it with the membership.
channel_preferences = sa.Table(
    "channel_preferences",
    metadata,
    sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["tenant_id", "workspace_id", "user_id"],
        ["memberships.tenant_id", "memberships.workspace_id", "memberships.user_id"],
        ondelete="CASCADE",
    ),
)

entitlements = sa.Table(
    "entitlements",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False, unique=True
    ),
    sa.Column("plan_id", sa.Text, sa.ForeignKey("plans.plan_id"), nullable=False),
    sa.Column("feature_overrides", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
)

approval_requests = sa.Table(
    "approval_requests",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("approval_type", sa.Text, nullable=False),
    sa.Column("requested_by", UUID(as_uuid=True), nullable=False),
    sa.Column("reason", sa.Text, nullable=False, server_default=""),
    sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("run_id", UUID(as_uuid=True), nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("version", sa.Integer, nullable=False, server_default="1"),
    # Its shape is checked by `ck_approval_requests_required_scope` (36dabf47619c,
    # or its twin 5d3965984679 here: whichever ran first).
    sa.Column("required_scope", sa.Text, nullable=True),
)

approval_decisions = sa.Table(
    "approval_decisions",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column(
        "request_id", UUID(as_uuid=True), sa.ForeignKey("approval_requests.id"), nullable=False
    ),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("decided_by", UUID(as_uuid=True), nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("comment", sa.Text, nullable=False, server_default=""),
    sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=False),
    # `web` or `zalo` (CHECK, migration dbb8c3359981): where the decision came from.
    sa.Column("channel", sa.Text, nullable=False, server_default="web"),
)

# A person opened an approval on the portal (zalo-channel ticket 05, ADR 0014):
# the approval's version and its subject's version at that moment.
approval_view_receipts = sa.Table(
    "approval_view_receipts",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("approval_id", UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), nullable=False),
    sa.Column("approval_version", sa.Integer, nullable=False),
    sa.Column("subject_version", sa.Text, nullable=True),
    sa.Column(
        "viewed_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# The single-use code a view issued; only its HMAC is stored.
approval_decision_codes = sa.Table(
    "approval_decision_codes",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("approval_id", UUID(as_uuid=True), nullable=False),
    sa.Column("user_id", UUID(as_uuid=True), nullable=False),
    sa.Column("receipt_id", UUID(as_uuid=True), nullable=False),
    sa.Column("code_hash", sa.LargeBinary, nullable=False),
    sa.Column("comment", sa.Text, nullable=False, server_default=""),
    sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("revoked_reason", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("resource_type", sa.Text, nullable=False),
    sa.Column("resource_id", sa.Text, nullable=False),
    sa.Column("run_id", UUID(as_uuid=True), nullable=True),
    sa.Column("policy_decision", sa.Text, nullable=True),
    sa.Column("trace_id", sa.Text, nullable=True),
    sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
)

feedback = sa.Table(
    "feedback",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("author_id", UUID(as_uuid=True), nullable=False),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    # Spec 003 US5: which part of the app, sent from which page, and what the
    # person would do about it. Nullable because rows predate the columns.
    sa.Column("module", sa.Text, nullable=True),
    sa.Column("page_path", sa.Text, nullable=True),
    sa.Column("suggestion", sa.Text, nullable=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

feedback_attachments = sa.Table(
    "feedback_attachments",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "feedback_id",
        UUID(as_uuid=True),
        sa.ForeignKey("feedback.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("object_key", sa.Text, nullable=False),
    sa.Column("content_type", sa.Text, nullable=False),
    sa.Column("size_bytes", sa.Integer, nullable=False),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

outbox_events = sa.Table(
    "outbox_events",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False),
    sa.Column("aggregate_id", UUID(as_uuid=True), nullable=False),
    sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column("correlation_id", UUID(as_uuid=True), nullable=True),
    sa.Column("causation_id", UUID(as_uuid=True), nullable=True),
    sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
    sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=False),
    sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("last_error", sa.Text, nullable=True),
)

# Replay cache for mutating HTTP requests that carry an `Idempotency-Key`
# header. The primary key is (tenant_id, idempotency_key), so the key string is
# the client's to choose within its own tenant and two tenants never collide —
# and inserting the row is itself the mutual exclusion between two concurrent
# requests presenting the same key.
idempotency_keys = sa.Table(
    "idempotency_keys",
    metadata,
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), primary_key=True),
    sa.Column("idempotency_key", sa.Text, primary_key=True),
    sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
    sa.Column("request_method", sa.Text, nullable=False),
    sa.Column("request_path", sa.Text, nullable=False),
    sa.Column("body_hash", sa.Text, nullable=False),
    # Both NULL while the first request is in flight; both set when it returned.
    sa.Column("response_status", sa.Integer, nullable=True),
    sa.Column("response_body", JSONB, nullable=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# Platform Operator allowlist: who may provision tenants (ADR-002). Global —
# no tenant_id, no RLS. Deliberately outside the tenant plane.
platform_operators = sa.Table(
    "platform_operators",
    metadata,
    sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
    sa.Column("note", sa.Text, nullable=True),
    sa.Column("created_by", UUID(as_uuid=True), nullable=True),
    sa.Column(
        "created_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

# Audit trail for provisioning actions. Global (a "create tenant" has no tenant
# to belong to yet), so it is separate from tenant-scoped audit_events.
provisioning_audit = sa.Table(
    "provisioning_audit",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=True),
    sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column(
        "occurred_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
)

# The handoff between the provisioner (files the request) and the worker's
# offboarding lane (exports + purges as dw_app, reports back). Migration
# 5d9d89ffc716.
tenant_offboarding_requests = sa.Table(
    "tenant_offboarding_requests",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="requested"),
    sa.Column("requested_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "requested_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("export_key", sa.Text, nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

policy_overrides = sa.Table(
    "policy_overrides",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("policy_id", sa.Text, nullable=False),
    sa.Column("content", JSONB, nullable=False),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# Separation of duties (migrations b9862fa13a80, 6b26771e549d). The rules are a
# platform catalogue the application reads; the membership trigger enforces
# them. A waiver is one tenant's recorded decision to lift a waivable rule.
sod_rules = sa.Table(
    "sod_rules",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("description", sa.Text, nullable=False),
    sa.Column("left_scopes", JSONB, nullable=False),
    sa.Column("right_scopes", JSONB, nullable=False),
    sa.Column("waivable", sa.Boolean, nullable=False, server_default=sa.text("false")),
)

sod_waivers = sa.Table(
    "sod_waivers",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("rule_key", sa.Text, sa.ForeignKey("sod_rules.key"), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("granted_by", UUID(as_uuid=True), nullable=False),
    sa.Column(
        "granted_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    sa.Column("revoked_by", UUID(as_uuid=True), nullable=True),
    sa.Column("revoke_reason", sa.Text, nullable=True),
)

# One person's in-app inbox (migration 855ae928c3fa). RLS narrows reads and
# updates to rows addressed to the bound principal.
notifications = sa.Table(
    "notifications",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("recipient_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("link", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
)

# A notification on its way out through a linked chat (migration 4a865a1c97aa,
# ADR 0013). Rows are created only by `platform.deliver_notification`; the
# application updates the delivery's own state and nothing else.
channel_deliveries = sa.Table(
    "channel_deliveries",
    metadata,
    sa.Column("id", UUID(as_uuid=True), primary_key=True),
    sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
    sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("recipient_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
    sa.Column("channel", sa.Text, nullable=False),
    sa.Column("source_key", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("link", sa.Text, nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="pending"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "next_attempt_at",
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("external_message_id", sa.Text, nullable=True),
    sa.Column("last_error", sa.Text, nullable=True),
    sa.Column(
        "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
    sa.Column(
        "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
    ),
)

# Tables whose rows belong to exactly one tenant → RLS enabled + forced.
TENANT_SCOPED_TABLES = (
    "workspaces",
    "memberships",
    "entitlements",
    "approval_requests",
    "approval_decisions",
    "audit_events",
    "outbox_events",
    "idempotency_keys",
)
