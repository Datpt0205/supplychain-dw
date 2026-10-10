"""supply chain proposal drafts

Revision ID: d4048e50d4a3
Revises: 988592a8100f
Create Date: 2026-10-06 21:00:58.456747+00:00

A product proposed through a chat (zalo-channel ticket 04, part Z4b) is built up
over several messages before it becomes a case. `supply_chain.proposal_drafts`
holds that conversation's state in the database rather than in a process's
memory, so a restart loses nothing and two workers agree on it.

- **One open draft per person, workspace and channel**: UNIQUE
  `(tenant_id, workspace_id, user_id, channel)`. The workspace is in the key on
  purpose: a draft hidden by workspace RLS must not block a new one after the
  person switches workspace on /settings.
- **FK to the membership** `(tenant_id, workspace_id, user_id)`, `ON DELETE
  CASCADE`, carried by the unique index that leads with the same columns: a
  draft only exists for someone who belongs where it is, and goes with the
  membership (offboarding included).
- **`draft` is versioned JSON** (`schema_version` inside, CHECKed present).
  `draft_version` bumps on every change; `summarized_version` is the version the
  last summary was sent for. A case is created only when they are equal, and
  creating it DELETEs the row guarded on `draft_version` in the case's own
  transaction, so a second "Đồng ý" creates nothing.
- **Workspace RLS**, ENABLEd and FORCEd, the one shape `CLAUDE.md` allows:
  `tenant AND (workspace OR app.workspace_scope = 'tenant')`. Plus
  `worker_drain_proposal_drafts`, the escape hatch the other sweeps use
  (`c3ec03bd6fd1`): the retention lane deletes expired drafts of every tenant in
  one statement, under `app.worker_drain` set per transaction by the sweep alone.
- **`updated_at`** by `platform.touch_updated_at()`, which yields to a value the
  statement states.
- **Grants ship here.** `dw_app` reads, inserts, deletes, and updates only the
  draft's own state; never its owner, tenant, workspace or channel.
- No SECURITY DEFINER function.
"""

from __future__ import annotations

from alembic import op

revision = "d4048e50d4a3"
down_revision = "988592a8100f"
branch_labels = None
depends_on = None

_TENANT = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
_WORKSPACE = "workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid"
_SCOPE = "current_setting('app.workspace_scope', true) = 'tenant'"
_POLICY = f"({_TENANT} AND ({_WORKSPACE} OR {_SCOPE}))"
_DRAIN = "current_setting('app.worker_drain', true) = 'on'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE supply_chain.proposal_drafts (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            user_id uuid NOT NULL,
            channel text NOT NULL,
            draft jsonb NOT NULL,
            draft_version integer NOT NULL,
            summarized_version integer,
            expires_at timestamp with time zone NOT NULL,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT pk_proposal_drafts PRIMARY KEY (id),
            CONSTRAINT uq_proposal_drafts_tenant_id_workspace_id_user_id_channel
                UNIQUE (tenant_id, workspace_id, user_id, channel),
            CONSTRAINT fk_proposal_drafts_tenant_id_memberships
                FOREIGN KEY (tenant_id, workspace_id, user_id)
                REFERENCES platform.memberships (tenant_id, workspace_id, user_id)
                ON DELETE CASCADE,
            CONSTRAINT ck_proposal_drafts_channel CHECK (channel IN ('zalo')),
            CONSTRAINT ck_proposal_drafts_draft CHECK (
                jsonb_typeof(draft) = 'object' AND draft ? 'schema_version'
            ),
            CONSTRAINT ck_proposal_drafts_draft_version CHECK (draft_version >= 1),
            CONSTRAINT ck_proposal_drafts_summarized_version CHECK (
                summarized_version IS NULL
                OR summarized_version BETWEEN 1 AND draft_version
            )
        )
        """
    )
    # The retention sweep deletes by expiry, across tenants.
    op.execute(
        "CREATE INDEX ix_proposal_drafts_expires_at ON supply_chain.proposal_drafts (expires_at)"
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON supply_chain.proposal_drafts"
        " FOR EACH ROW EXECUTE FUNCTION platform.touch_updated_at()"
    )
    op.execute("ALTER TABLE supply_chain.proposal_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE supply_chain.proposal_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_proposal_drafts ON supply_chain.proposal_drafts"
        f" USING {_POLICY} WITH CHECK {_POLICY}"
    )
    op.execute(
        "CREATE POLICY worker_drain_proposal_drafts ON supply_chain.proposal_drafts"
        f" USING ({_DRAIN}) WITH CHECK ({_DRAIN})"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dw_app') THEN
                REVOKE ALL ON supply_chain.proposal_drafts FROM dw_app;
                GRANT SELECT, INSERT, DELETE ON supply_chain.proposal_drafts TO dw_app;
                GRANT UPDATE (draft, draft_version, summarized_version, expires_at)
                    ON supply_chain.proposal_drafts TO dw_app;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS supply_chain.proposal_drafts")
