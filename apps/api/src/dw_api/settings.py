"""Runtime settings; secrets only via environment (never hardcoded)."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from dw_knowledge.contracts import DEFAULT_COLLECTION

# Four profiles, two of them deployed.
#
#   local       a developer's machine. Mocks allowed, docs exposed, private
#               outbound targets allowed (Ollama/vLLM on localhost).
#   test        CI. Same permissiveness as local; no external services assumed.
#   uat         a real deployment that real people sign into with real data.
#               Held to production's rules, because the difference between UAT
#               and production is who is affected by a mistake, not how strict
#               the configuration is. A mock model or a dev-token issuer in UAT
#               is the same defect it would be in production.
#   production  the live deployment.
#
# Anything that must not reach a deployed environment is gated on
# ``is_deployed`` rather than on ``profile == "production"``, so adding a fifth
# environment later cannot silently reopen a hole.
Profile = Literal["local", "test", "uat", "production"]
DEPLOYED_PROFILES: frozenset[str] = frozenset({"uat", "production"})

AuthMode = Literal["dev", "oidc"]


class ApiSettings(BaseSettings):
    """API process configuration, validated at startup.

    In a deployed profile, unknown critical conditions fail fast; mocks and the
    development identity adapter are forbidden.
    """

    model_config = SettingsConfigDict(env_prefix="DW_API_", extra="ignore", populate_by_name=True)

    profile: Profile = "local"
    host: str = "0.0.0.0"  # nosec B104  # containerized API must bind all interfaces
    port: int = Field(default=8000, ge=1, le=65535)
    database_url: str | None = None
    # The provisioning role's connection: a separate role that can write across
    # tenants but reads no business schema. Absent means /api/v1/platform/* is
    # not mounted.
    provisioner_database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DW_API_PROVISIONER_DATABASE_URL", "DW_PROVISIONER_DATABASE_URL"
        ),
    )
    # The request-serving pool. SQLAlchemy's default (5/10) serialises past 15
    # concurrent DB-touching requests in an async app; 10/20 gives real headroom
    # and stays well under Postgres' default max_connections across all pools.
    db_pool_size: int = Field(
        default=10, ge=1, le=100, validation_alias=AliasChoices("DW_API_DB_POOL_SIZE")
    )
    db_max_overflow: int = Field(
        default=20, ge=0, le=200, validation_alias=AliasChoices("DW_API_DB_MAX_OVERFLOW")
    )
    cors_origins: list[str] = []

    # Valkey/Redis for caching (AccessContext, etc.). Absent → no cache, every
    # read hits the database. Compose sets REDIS_URL; the bare alias accepts it.
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_REDIS_URL", "REDIS_URL"),
    )

    # --- authentication ---
    auth_mode: AuthMode = "dev"
    dev_secret: str | None = None
    oidc_issuer_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_OIDC_ISSUER_URL", "OIDC_ISSUER_URL")
    )
    oidc_audience: str = "dw-api"
    # JWKS endpoint used to fetch signing keys. Decoupled from the issuer so the
    # API (inside Docker) can reach the IdP on the internal network while the
    # token issuer stays the browser-facing hostname. Defaults to
    # ``{issuer}/protocol/openid-connect/certs`` when unset.
    oidc_jwks_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_OIDC_JWKS_URL", "OIDC_JWKS_URL")
    )

    # --- first-login provisioning ---
    default_tenant_id: str = Field(
        default="d6b43d0e-c3c6-5dbc-bc08-150621bd9a5d",
        validation_alias=AliasChoices("DW_API_DEFAULT_TENANT_ID"),
    )
    default_workspace_id: str = Field(
        default="64764894-718d-5558-ba17-9a2949214063",
        validation_alias=AliasChoices("DW_API_DEFAULT_WORKSPACE_ID"),
    )
    default_role: str = Field(
        default="member", validation_alias=AliasChoices("DW_API_DEFAULT_ROLE")
    )
    # Default-deny: a brand-new SSO identity gets no membership and must be
    # granted access by an admin. Turn on only for a self-serve tenant.
    auto_provision_default_membership: bool = Field(
        default=False,
        validation_alias=AliasChoices("DW_API_AUTO_PROVISION_MEMBERSHIP"),
    )

    # --- artifact storage (MinIO/S3) ---
    s3_endpoint_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_S3_ENDPOINT_URL", "S3_ENDPOINT_URL")
    )
    s3_access_key: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_S3_ACCESS_KEY", "MINIO_ROOT_USER")
    )
    s3_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_S3_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
    )
    s3_bucket: str = Field(
        default="dw-artifacts",
        # Both are env-var NAMES, not a key. The scanner reads the pair as an
        # assignment, hence the allow.
        validation_alias=AliasChoices("DW_API_S3_BUCKET", "S3_BUCKET_ARTIFACTS"),  # gitleaks:allow
    )
    # Platform attachments (feedback screenshots) live in a bucket of their own,
    # so their reads and retention never mix with knowledge artifacts.
    feedback_bucket: str = Field(
        default="feedback", validation_alias=AliasChoices("DW_API_FEEDBACK_BUCKET")
    )
    # Supply Chain case documents (ADR 0021): a bucket of their own, the same
    # name the worker reads (`dw_worker.settings.case_documents_bucket`) for
    # offboarding and the orphan sweep.
    case_documents_bucket: str = Field(
        default="case-documents",
        validation_alias=AliasChoices("DW_API_CASE_DOCUMENTS_BUCKET", "CASE_DOCUMENTS_BUCKET"),
    )
    # The largest case document one upload may carry. The upload route refuses
    # a request body over it (plus the multipart framing) from its headers,
    # before the form is parsed, and holds at most one byte past it in memory.
    case_document_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1,
        le=200 * 1024 * 1024,
        validation_alias=AliasChoices("DW_API_CASE_DOCUMENT_MAX_BYTES"),
    )

    # --- vector store ---
    qdrant_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_QDRANT_URL", "QDRANT_URL")
    )
    qdrant_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_QDRANT_API_KEY", "QDRANT_API_KEY"),
    )
    # Fixed at collection-creation time, so moving to another embedding model
    # means naming a new collection and reindexing into it, never editing this.
    qdrant_collection: str = Field(
        default=DEFAULT_COLLECTION,
        validation_alias=AliasChoices("DW_API_QDRANT_COLLECTION", "QDRANT_COLLECTION"),
    )

    # --- embeddings / rerank ---
    # "hash" (offline default) | "tei" (self-hosted) | "openai_compatible"
    # (the configured gateway; model and width come from the model profile).
    embedding_provider: str = Field(
        default="hash", validation_alias=AliasChoices("DW_API_EMBEDDING_PROVIDER")
    )
    embed_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_EMBED_URL", "TEI_EMBED_URL")
    )
    rerank_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_RERANK_URL", "TEI_RERANK_URL")
    )
    embed_dimension: int = Field(
        default=1024, validation_alias=AliasChoices("DW_API_EMBED_DIMENSION")
    )

    # --- model provider ---
    model_profile: str = Field(
        default="balanced",
        validation_alias=AliasChoices("DW_API_MODEL_PROFILE", "DW_MODEL_PROFILE_ID"),
    )
    model_provider: str = Field(
        default="mock", validation_alias=AliasChoices("DW_API_MODEL_PROVIDER", "DW_MODEL_PROVIDER")
    )
    openai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    openai_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_OPENAI_BASE_URL", "OPENAI_BASE_URL")
    )
    openai_structured_mode: str = Field(
        default="json_schema",
        validation_alias=AliasChoices("DW_API_OPENAI_STRUCTURED_MODE"),
    )
    # Responses-API strict structured output. On by default because /responses
    # validates the schema either way: a Pydantic model with a defaulted field
    # omits it from `required`, and the endpoint answers 400. `strict` is what
    # makes the adapter fill it in. Two processes run the same graphs, so a
    # default they disagree on is a graph that works in one and not the other.
    openai_strict_schema: bool = Field(
        default=True,
        validation_alias=AliasChoices("DW_API_OPENAI_STRICT_SCHEMA"),
    )

    # --- hardening ---
    rate_limit_per_minute: int = Field(
        default=240,
        ge=0,  # 0 disables (tests); per-caller fixed window
        validation_alias=AliasChoices("DW_API_RATE_LIMIT_PER_MINUTE"),
    )
    outbound_allowed_hosts: list[str] = Field(
        default=[],
        validation_alias=AliasChoices("DW_API_OUTBOUND_ALLOWED_HOSTS"),
    )

    # --- task connector ---
    # "none" (nothing integrated) | "mock" (test placeholder).
    task_connector: str = Field(
        default="mock",
        validation_alias=AliasChoices("DW_API_TASK_CONNECTOR", "DW_TASK_CONNECTOR"),
    )

    public_web_url: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("DW_API_PUBLIC_WEB_URL", "DW_PUBLIC_WEB_URL"),
    )
    approval_reminder_seconds: int = Field(
        default=5,
        ge=1,
        le=604800,
        validation_alias=AliasChoices(
            "DW_API_APPROVAL_REMINDER_SECONDS", "DW_APPROVAL_REMINDER_SECONDS"
        ),
    )

    # --- Zalo self-link (zalo-channel ticket 01) ---
    # One bot per deployment. The token is a credential (it rides in every Bot
    # API URL); the link secret signs the one-time ``/start`` token and must be
    # the worker's value too. Both unset = the /zalo routes are not mounted.
    # ``SecretStr`` so neither prints in a repr, a log line or a validation error.
    zalo_bot_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DW_API_ZALO_BOT_TOKEN", "ZALO_BOT_TOKEN"),
    )
    zalo_link_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DW_API_ZALO_LINK_SECRET", "ZALO_LINK_SECRET"),
    )
    # Deep link to the bot's chat shown on the settings page; empty = none.
    zalo_bot_link: str = Field(
        default="", validation_alias=AliasChoices("DW_API_ZALO_BOT_LINK", "ZALO_BOT_LINK")
    )

    @property
    def zalo_link_enabled(self) -> bool:
        """Linking needs both: a token to reply with and a secret to sign with."""
        return bool(
            self.zalo_bot_token.get_secret_value() and self.zalo_link_secret.get_secret_value()
        )

    # --- observability (Langfuse optional behind configuration) ---
    otel_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_OTEL_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    langfuse_enabled: bool = Field(
        default=False, validation_alias=AliasChoices("DW_API_LANGFUSE_ENABLED", "LANGFUSE_ENABLED")
    )
    langfuse_host: str | None = Field(
        default=None, validation_alias=AliasChoices("DW_API_LANGFUSE_HOST", "LANGFUSE_HOST")
    )
    langfuse_public_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DW_API_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY"),
    )

    @property
    def is_deployed(self) -> bool:
        """True for profiles real people sign into (``uat``, ``production``)."""
        return self.profile in DEPLOYED_PROFILES

    def outbound_allow_private(self) -> bool:
        """Local/test may call localhost providers; a deployed profile never does."""
        return not self.is_deployed

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DW_API_DATABASE_URL is not configured; the API cannot serve tenant data"
            )
        return self.database_url

    def validate_for_profile(self) -> None:
        """Fail fast on configurations that must never reach a deployed profile."""
        if self.is_deployed:
            if self.auth_mode == "dev":
                raise RuntimeError(f"dev auth mode is forbidden in the {self.profile} profile")
            if not self.oidc_issuer_url:
                raise RuntimeError(f"an OIDC issuer is required in the {self.profile} profile")
            self.require_database_url()
            if self.model_provider == "mock":
                raise RuntimeError(
                    f"the mock model provider is forbidden in the {self.profile} profile"
                )
            # `mock` is a test placeholder; `none` (no external task system
            # wired) is a legitimate deployed choice.
            if self.task_connector == "mock":
                raise RuntimeError(
                    f"the mock task connector is forbidden in the {self.profile} profile — "
                    "use 'none' if no external task system is integrated"
                )
            if self.embedding_provider == "hash":
                raise RuntimeError(
                    "the hash embedding provider carries no meaning and is forbidden in the "
                    f"{self.profile} profile — configure 'tei' or 'openai_compatible'"
                )
            if not self.qdrant_url:
                raise RuntimeError(
                    "a vector store is required in the "
                    f"{self.profile} profile — the in-memory index is not durable"
                )
            if not self.cors_origins:
                raise RuntimeError(
                    f"CORS origins must be listed explicitly in the {self.profile} profile"
                )
        if self.auth_mode == "oidc" and not self.oidc_issuer_url:
            raise RuntimeError("auth_mode=oidc requires DW_API_OIDC_ISSUER_URL")
        if self.langfuse_enabled and not (
            self.langfuse_host and self.langfuse_public_key and self.langfuse_secret_key
        ):
            raise RuntimeError(
                "LANGFUSE_ENABLED requires LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY "
                "and LANGFUSE_SECRET_KEY"
            )
        if self.task_connector not in ("none", "mock"):
            raise RuntimeError("DW_TASK_CONNECTOR must be 'none' or 'mock'")
