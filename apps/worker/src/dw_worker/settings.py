"""Worker process settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from dw_knowledge.contracts import DEFAULT_COLLECTION
from dw_knowledge.ingest_jobs import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    DEFAULT_LEASE_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
)

# Mirrors dw_api.settings: the same four profiles, and the same rule that
# anything forbidden in production is forbidden in UAT, because the difference
# between them is who a mistake reaches, not how strict the configuration is.
Profile = Literal["local", "test", "uat", "production"]
DEPLOYED_PROFILES: frozenset[str] = frozenset({"uat", "production"})


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DW_WORKER_",
        extra="ignore",
        populate_by_name=True,
    )

    profile: Profile = Field(
        default="local",
        validation_alias=AliasChoices("DW_WORKER_PROFILE", "DW_API_PROFILE"),
    )

    # Where configs/, evals/fixtures/ and the rest of the repo live. In dev this
    # is the source tree; the image ships them at /app and sets DW_REPO_ROOT.
    repo_root: Path = Field(
        default=Path(__file__).resolve().parents[4],
        validation_alias=AliasChoices("DW_WORKER_REPO_ROOT", "DW_REPO_ROOT"),
    )

    heartbeat_file: Path = Path("/tmp/dw-worker-heartbeat")  # nosec B108  # container liveness probe path
    heartbeat_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    # How often Supply Chain's follow-up sweep runs. Five minutes by default;
    # a tester sets 60 to see a reminder land within the minute.
    supply_chain_follow_up_interval_seconds: float = Field(default=300.0, ge=10, le=86400)
    # How often the BGĐ review reconcile looks for product cases waiting with
    # no review. Five minutes: a review a start missed waits at most that long.
    supply_chain_product_review_reconcile_interval_seconds: float = Field(
        default=300.0, ge=10, le=86400
    )

    # Prometheus scrape target for this process (Ops hardening Phase 5). 9464
    # is the OTel/Prometheus exporter's own convention default; dw-api has no
    # equivalent setting because it serves `/metrics` on its existing HTTP port.
    metrics_port: int = Field(default=9464, ge=1, le=65535)

    # Infrastructure for the knowledge ingest consumer (B5). When database_url /
    # s3_endpoint_url are unset, the consumer is skipped (heartbeat-only worker).
    # database_url takes no alias: DW_DATABASE_URL names the migrator connection,
    # which holds BYPASSRLS and would disable every row-level policy silently.
    # The other fields still accept the plain env vars compose already provides.
    database_url: str | None = None
    qdrant_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_WORKER_QDRANT_URL", "QDRANT_URL")
    )
    # Required when the server enforces QDRANT__SERVICE__API_KEY (shared dev
    # server override); None keeps plain local Qdrant working.
    qdrant_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_QDRANT_API_KEY", "QDRANT_API_KEY"),
    )
    s3_endpoint_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_S3_ENDPOINT_URL", "S3_ENDPOINT_URL"),
    )
    s3_access_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_S3_ACCESS_KEY", "MINIO_ROOT_USER"),
    )
    s3_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_S3_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    )
    s3_bucket: str = Field(
        default="dw-artifacts",
        validation_alias=AliasChoices("DW_WORKER_S3_BUCKET", "S3_BUCKET_ARTIFACTS"),
    )
    # Tenant offboarding's export destination — the bucket already exists
    # (infra/compose/docker-compose.yml's minio-setup), matched here rather
    # than a new one made for this feature.
    s3_bucket_exports: str = Field(
        default="dw-exports",
        validation_alias=AliasChoices("DW_WORKER_S3_BUCKET_EXPORTS", "S3_BUCKET_EXPORTS"),
    )
    # Same bucket dw_api's feedback attachments live in (dw_api/settings.py's
    # `feedback_bucket`) — offboarding needs to read and delete a tenant's
    # attachments from the same place they were written to.
    feedback_bucket: str = Field(
        default="feedback", validation_alias=AliasChoices("DW_WORKER_FEEDBACK_BUCKET")
    )
    # Same bucket dw_api writes case documents to (dw_api/settings.py's
    # `case_documents_bucket`, ADR 0021): offboarding exports and deletes a
    # tenant's documents there, and the orphan sweep clears keys no row holds.
    case_documents_bucket: str = Field(
        default="case-documents",
        validation_alias=AliasChoices("DW_WORKER_CASE_DOCUMENTS_BUCKET", "CASE_DOCUMENTS_BUCKET"),
    )

    # Which Qdrant collection this process reads and writes. Named explicitly
    # because the collection's vector width is fixed at creation: moving to a
    # different embedding model means a new collection and a reindex, and
    # pointing two widths at one name is refused rather than resolved.
    qdrant_collection: str = Field(
        default=DEFAULT_COLLECTION,
        validation_alias=AliasChoices("DW_WORKER_QDRANT_COLLECTION", "QDRANT_COLLECTION"),
    )

    # "hash" (offline default) | "tei" (self-hosted) | "openai_compatible"
    # (the configured gateway; model and width come from the profile below).
    embedding_provider: str = Field(
        default="hash", validation_alias=AliasChoices("DW_WORKER_EMBEDDING_PROVIDER")
    )
    model_profile: str = Field(
        default="gateway",
        validation_alias=AliasChoices("DW_WORKER_MODEL_PROFILE", "DW_API_MODEL_PROFILE"),
    )
    # Observability → Langfuse (OTLP). Empty = NullTelemetry (no export). Same four
    # knobs api/chat read, so every research/scoring/news run traces to one project.
    otel_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_OTEL_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    langfuse_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("DW_WORKER_LANGFUSE_ENABLED", "LANGFUSE_ENABLED"),
    )
    langfuse_host: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_WORKER_LANGFUSE_HOST", "LANGFUSE_HOST")
    )
    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_WORKER_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY"),
    )
    openai_base_url: str = Field(
        default="", validation_alias=AliasChoices("DW_WORKER_OPENAI_BASE_URL", "OPENAI_BASE_URL")
    )
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("DW_WORKER_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    # Reads uploaded documents and images. Verified per modality, not chosen by
    # tier: gpt-4.1-mini reads a PDF correctly through this gateway but does NOT
    # receive images - asked the colour of a solid blue square it answered
    # "Green", and asked to transcribe one it invented a book cover. A model
    # that cannot see the image still answers, so this is only ever caught by
    # asking it something the image alone can answer.
    extraction_model: str = Field(
        default="gpt-5.6-luna", validation_alias=AliasChoices("DW_WORKER_EXTRACTION_MODEL")
    )
    deepgram_api_key: str = Field(
        default="", validation_alias=AliasChoices("DW_WORKER_DEEPGRAM_API_KEY", "DEEPGRAM_API_KEY")
    )
    embed_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_WORKER_EMBED_URL", "TEI_EMBED_URL")
    )
    rerank_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_WORKER_RERANK_URL", "TEI_RERANK_URL")
    )
    embed_dimension: int = Field(
        default=1024, validation_alias=AliasChoices("DW_WORKER_EMBED_DIMENSION")
    )

    # Jobs drained concurrently per tick. More than one because a single slow
    # file must not hold up every other tenant's uploads.
    ingest_batch_size: int = Field(default=4, ge=1, le=16)
    # How long a claim is held. Must exceed the job timeout below, or a second
    # worker takes over a job the first is still running.
    ingest_lease_seconds: int = Field(default=DEFAULT_LEASE_SECONDS, ge=60, le=7200)
    # No longer capped by the lease: the consumer renews the lease on a
    # heartbeat, so this is the budget for the work itself. A four-hour customer
    # meeting is a real recording and needs tens of minutes end to end.
    ingest_job_timeout_seconds: float = Field(default=DEFAULT_JOB_TIMEOUT_SECONDS, gt=0, le=21600)
    ingest_heartbeat_seconds: float = Field(default=DEFAULT_HEARTBEAT_SECONDS, gt=0, le=3600)
    ingest_retry_backoff_seconds: float = Field(
        default=DEFAULT_RETRY_BACKOFF_SECONDS, gt=0, le=3600
    )

    # Outbox events are cheap to dispatch, so a batch keeps an import of many
    # accounts from taking one poll tick each.
    outbox_batch_size: int = Field(default=8, ge=1, le=64)
    # After this many delivery attempts an event stops being claimed and waits
    # for a human, with `last_error` saying what it kept failing on. Three is a
    # transient fault survived twice, not a broken handler retried for ever.
    outbox_max_attempts: int = Field(default=3, ge=1, le=10)

    # --- Zalo self-link (zalo-channel ticket 01) ---
    # The bot token is a credential (it rides in every Bot API URL) and the link
    # secret must equal the API's: it verifies the ``/start`` token the API
    # signed. ``SecretStr`` so neither prints in a repr, a log or an error.
    zalo_bot_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DW_WORKER_ZALO_BOT_TOKEN", "ZALO_BOT_TOKEN"),
    )
    zalo_link_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DW_WORKER_ZALO_LINK_SECRET", "ZALO_LINK_SECRET"),
    )
    # poll = this process long-polls getUpdates (no public URL needed);
    # webhook = Zalo POSTs to the API and nothing here polls. One bot answers
    # one reader: two processes polling the same bot steal each other's updates.
    zalo_updates_mode: Literal["poll", "webhook"] = Field(
        default="poll",
        validation_alias=AliasChoices("DW_WORKER_ZALO_UPDATES_MODE", "ZALO_UPDATES_MODE"),
    )
    # What the bot calls this deployment in its replies ("tài khoản <name>").
    product_name: str = Field(
        default="Digital Worker",
        min_length=1,
        validation_alias=AliasChoices("DW_WORKER_PRODUCT_NAME", "DW_PRODUCT_NAME"),
    )

    @property
    def zalo_poll_enabled(self) -> bool:
        """Poll only with a token to poll with and a secret to verify with."""
        return (
            self.zalo_updates_mode == "poll"
            and bool(self.zalo_bot_token.get_secret_value())
            and bool(self.zalo_link_secret.get_secret_value())
        )

    @model_validator(mode="after")
    def _the_heartbeat_outruns_the_lease(self) -> WorkerSettings:
        """Caught at startup, because the failure mode is a silent double bill.

        The job budget used to be capped by the lease, which capped every upload
        at the shortest lease anybody would wait out - and a customer meeting
        runs three or four hours. The consumer now renews the lease while it
        works, so the budget is free and this is the invariant that replaces it:
        a heartbeat slower than the lease renews nothing. The lease lapses
        between beats, a second worker claims a job the first is still running,
        and the same file is parsed and transcribed twice - both writes landing
        on the same deterministic document id, so nothing downstream looks wrong.
        """
        if self.ingest_heartbeat_seconds >= self.ingest_lease_seconds:
            raise RuntimeError(
                "DW_WORKER_INGEST_HEARTBEAT_SECONDS must be shorter than "
                "DW_WORKER_INGEST_LEASE_SECONDS"
            )
        return self

    @property
    def is_deployed(self) -> bool:
        """True for profiles real people's data flows through."""
        return self.profile in DEPLOYED_PROFILES

    def validate_for_profile(self) -> None:
        """Fail fast on configurations that must never reach a deployed profile."""
        if not self.is_deployed:
            return
        if not self.database_url:
            raise RuntimeError(f"a database is required in the {self.profile} profile")
        if self.embedding_provider == "hash":
            raise RuntimeError(
                "the hash embedding provider carries no meaning and is forbidden in the "
                f"{self.profile} profile - configure 'tei' or 'openai_compatible'"
            )
        if not self.qdrant_url:
            raise RuntimeError(
                f"a vector store is required in the {self.profile} profile - "
                "the in-memory index is not durable"
            )
