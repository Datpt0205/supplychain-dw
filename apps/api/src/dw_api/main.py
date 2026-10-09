"""FastAPI application factory.

Platform routers are mounted unconditionally; the ones that need infrastructure
check their dependency first, so a host without a database serves fewer routes
rather than routes that fail on every call.

Business bounded contexts mount their presentation routers at the marked seam
near the bottom, built from ``container.runtime`` (see
``dw_api.bootstrap.wiring``). Nothing in this module may import a business
package.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

import dw_api
from dw_api.bootstrap import ApiContainer, build_container
from dw_api.exception_handlers import register_exception_handlers
from dw_api.middleware.rate_limit import RateLimitMiddleware
from dw_api.middleware.request_id import RequestIdMiddleware
from dw_api.routes.v1.admin_console import router as admin_console_router
from dw_api.routes.v1.admin_members import invitations_router as admin_invitations_router
from dw_api.routes.v1.admin_members import router as admin_members_router
from dw_api.routes.v1.approvals import router as approvals_router
from dw_api.routes.v1.audit import router as audit_router
from dw_api.routes.v1.auth import router as auth_router
from dw_api.routes.v1.directory import router as directory_router
from dw_api.routes.v1.feedback import router as feedback_router
from dw_api.routes.v1.health import build_health_router
from dw_api.routes.v1.integrations import router as integrations_router
from dw_api.routes.v1.knowledge import router as knowledge_router
from dw_api.routes.v1.me import router as me_router
from dw_api.routes.v1.memory import router as memory_router
from dw_api.routes.v1.notifications import router as notifications_router
from dw_api.routes.v1.platform import router as platform_router
from dw_api.routes.v1.runs import router as runs_router
from dw_api.routes.v1.support import router as support_router
from dw_api.routes.v1.zalo import router as zalo_router
from dw_api.routes.v1.zalo import webhook_router as zalo_webhook_router

_LOG = logging.getLogger(__name__)

# Headers the browser client sends. Listed rather than wildcarded because a
# wildcard and credentials cannot both be allowed.
_CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "X-Tenant-Id",
    "X-Workspace-Id",
    # The customer's grant a support staff member acts under (ADR 0024).
    "X-DW-Support-Grant",
    "Idempotency-Key",
]
_CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _local_web_origins(public_web_url: str) -> list[str]:
    """The dev web origin, read from the one setting that names the web app.

    A hard-coded port here was a second copy of `DW_PUBLIC_WEB_URL`: moving the
    web to another port left the API refusing its own front end. Both host
    spellings are allowed because browsers treat them as different origins.
    """
    parts = urlsplit(public_web_url)
    origin = f"{parts.scheme}://{parts.netloc}"
    swap = {"localhost": "127.0.0.1", "127.0.0.1": "localhost"}
    other = origin.replace(
        parts.hostname or "", swap.get(parts.hostname or "", parts.hostname or ""), 1
    )
    return list(dict.fromkeys([origin, other]))


def create_app(container: ApiContainer | None = None) -> FastAPI:
    container = container or build_container()
    settings = container.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One LISTEN connection for this process, feeding every open stream.
        # Started here because it owns a connection for the app's lifetime, and
        # a composition root that is synchronous cannot open one.
        if container.run_events is not None:
            await container.run_events.start()
        try:
            yield
        finally:
            await container.shutdown()

    # The schema and its Swagger/ReDoc UIs describe every route to anyone who
    # asks; deployed profiles hide them so a scan cannot enumerate the surface.
    # Kept in local/test, where they are the tool developers actually use.
    hide_docs = settings.is_deployed
    app = FastAPI(
        title="Digital Worker Platform API",
        version=dw_api.__version__,
        lifespan=lifespan,
        docs_url=None if hide_docs else "/api/docs",
        redoc_url=None if hide_docs else "/api/redoc",
        openapi_url=None if hide_docs else "/api/openapi.json",
    )
    app.state.container = container

    # Middleware runs in reverse registration order: request-id first, then limit.
    app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
    app.add_middleware(RequestIdMiddleware)

    # Browser clients are cross-origin; a deployed profile must list origins
    # explicitly, local/test fall back to the dev web origin.
    cors_origins = settings.cors_origins
    if not cors_origins and not settings.is_deployed:
        cors_origins = _local_web_origins(settings.public_web_url)
    if cors_origins:
        from starlette.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=_CORS_METHODS,
            allow_headers=_CORS_HEADERS,
            expose_headers=["X-Request-ID", "Retry-After"],
        )

    register_exception_handlers(app)

    # Root-level, not `/api/v1`: this is a Prometheus scrape target, not a
    # platform API — the endpoint stays fixed regardless of API versioning.
    # Unauthenticated by design; the trust boundary is network segmentation
    # (only `dw-internal`, where Prometheus lives, can reach it), the same
    # boundary every other inter-container call in this compose file relies on.
    # A plain route rather than `app.mount(make_asgi_app())`: a sub-mount
    # 307-redirects a bare `GET /metrics` (no trailing slash) to `/metrics/`,
    # which is not what a scrape config asking for `/metrics` expects.
    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(build_health_router(container.health_service), prefix="/api/v1")
    for router in (
        auth_router,
        me_router,
        notifications_router,
        directory_router,
        admin_members_router,
        admin_invitations_router,
        admin_console_router,
        approvals_router,
        runs_router,
        audit_router,
        feedback_router,
        knowledge_router,
        memory_router,
        integrations_router,
        support_router,
    ):
        app.include_router(router, prefix="/api/v1")

    # The user's own Zalo link: mounted only when the bot token and the link
    # secret are both configured, so an unconfigured deployment answers 404.
    if container.zalo_linking is not None:
        app.include_router(zalo_router, prefix="/api/v1")

    # Zalo's own door, the hosted way updates arrive (ADR 0008): only in
    # webhook mode with a secret set. In poll mode the worker reads the bot
    # and this route does not exist, so the two never both read one bot.
    if settings.zalo_webhook_enabled and container.zalo_webhook_inbox is not None:
        app.include_router(zalo_webhook_router, prefix="/api/v1")

    # Platform provisioning: mounted only when the provisioner connection is
    # configured, so environments that never provision stay lean.
    if container.provisioning is not None:
        app.include_router(platform_router, prefix="/api/v1")

    # ---- BOUNDED CONTEXT ROUTERS MOUNT HERE ------------------------------
    # Guarded on the dependency it needs: a context whose wiring is absent
    # mounts nothing rather than mounting a route that 500s on every call.
    if (
        container.supply_chain_create_po_case is not None
        and container.supply_chain_create_po is not None
        and container.supply_chain_reassign_po_case_pic is not None
        and container.supply_chain_get_po_case is not None
        and container.supply_chain_list_po_cases is not None
        and container.supply_chain_submit_supplier_update is not None
        and container.supply_chain_list_supplier_updates is not None
        and container.supply_chain_analyze_delay_impact is not None
        and container.supply_chain_list_delay_impact_analyses is not None
        and container.supply_chain_get_missing_update_status is not None
        and container.supply_chain_advance_po_case is not None
        and container.supply_chain_list_case_transitions is not None
        and container.supply_chain_list_case_approvals is not None
        and container.supply_chain_get_sla_evaluation is not None
        and container.supply_chain_get_sla_policy is not None
        and container.supply_chain_set_sla_policy_override is not None
        and container.supply_chain_get_approval_matrix is not None
        and container.supply_chain_set_approval_matrix_override is not None
        and container.supply_chain_get_attention_queue is not None
        and container.supply_chain_get_portfolio_summary is not None
        and container.supply_chain_answer_case_query is not None
        and container.supply_chain_get_daily_brief is not None
        and container.supply_chain_get_brief_policy is not None
        and container.supply_chain_set_brief_policy_override is not None
        and container.supply_chain_summarize_daily_brief is not None
        and container.supply_chain_get_action_duties is not None
        and container.supply_chain_set_action_duties_override is not None
        and container.supply_chain_list_follow_ups is not None
        and container.supply_chain_close_follow_up is not None
        and container.supply_chain_get_follow_up_policy is not None
        and container.supply_chain_set_follow_up_policy_override is not None
    ):
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.routes import build_router as build_supply_chain_router

        app.include_router(
            build_supply_chain_router(
                container.supply_chain_create_po_case,
                container.supply_chain_get_po_case,
                container.supply_chain_list_po_cases,
                container.supply_chain_submit_supplier_update,
                container.supply_chain_list_supplier_updates,
                container.supply_chain_analyze_delay_impact,
                container.supply_chain_list_delay_impact_analyses,
                container.supply_chain_get_missing_update_status,
                container.supply_chain_advance_po_case,
                container.supply_chain_list_case_transitions,
                container.supply_chain_get_sla_evaluation,
                container.supply_chain_get_sla_policy,
                container.supply_chain_set_sla_policy_override,
                container.supply_chain_get_approval_matrix,
                container.supply_chain_set_approval_matrix_override,
                container.supply_chain_get_attention_queue,
                container.supply_chain_get_portfolio_summary,
                container.supply_chain_answer_case_query,
                container.supply_chain_get_daily_brief,
                container.supply_chain_get_brief_policy,
                container.supply_chain_set_brief_policy_override,
                container.supply_chain_summarize_daily_brief,
                container.supply_chain_get_action_duties,
                container.supply_chain_set_action_duties_override,
                container.supply_chain_list_follow_ups,
                container.supply_chain_close_follow_up,
                container.supply_chain_get_follow_up_policy,
                container.supply_chain_set_follow_up_policy_override,
                create_po=container.supply_chain_create_po,
                reassign_pic=container.supply_chain_reassign_po_case_pic,
                list_case_approvals=container.supply_chain_list_case_approvals,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Case documents (ADR 0021): their own router on their own guard, rather
    # than three more arguments to the chain above.
    if (
        container.supply_chain_upload_case_document is not None
        and container.supply_chain_list_case_documents is not None
        and container.supply_chain_download_case_document is not None
    ):
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_form_idempotent_operation
        from dw_supply_chain.presentation.document_routes import build_documents_router

        app.include_router(
            build_documents_router(
                container.supply_chain_upload_case_document,
                container.supply_chain_list_case_documents,
                container.supply_chain_download_case_document,
                resolve_access_context=get_access_context,
                resolve_form_idempotency=get_form_idempotent_operation,
            )
        )

    # Step 12's colour, packaging and pre-production sub-flow (slice PK): its
    # own router on its own guard, as the documents router is.
    if (
        container.supply_chain_get_packaging_design is not None
        and container.supply_chain_take_packaging_step is not None
        and container.supply_chain_get_packaging_policy is not None
        and container.supply_chain_set_packaging_policy_override is not None
    ):
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.packaging_routes import build_packaging_router

        app.include_router(
            build_packaging_router(
                container.supply_chain_get_packaging_design,
                container.supply_chain_take_packaging_step,
                container.supply_chain_get_packaging_policy,
                container.supply_chain_set_packaging_policy_override,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Commercial data and BM04 as fields (ADR 0026, ticket ai-automation/01):
    # its own router on its own guard, as the documents router is.
    if container.supply_chain_commercial is not None:
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.commercial_routes import build_commercial_router

        app.include_router(
            build_commercial_router(
                container.supply_chain_commercial,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Document drafts and templates (ticket ai-automation/03): own router.
    if container.supply_chain_drafts is not None:
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.draft_routes import build_draft_router

        app.include_router(
            build_draft_router(
                container.supply_chain_drafts,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Step proposals (ticket ai-automation/05): own router.
    if container.supply_chain_step_proposals is not None:
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.step_proposal_routes import build_step_proposal_router

        app.include_router(
            build_step_proposal_router(
                container.supply_chain_step_proposals,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Messages to a supplier (ticket ai-automation/07): own router.
    if container.supply_chain_supplier_messages is not None:
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.supplier_message_routes import (
            build_supplier_message_router,
        )

        app.include_router(
            build_supplier_message_router(
                container.supply_chain_supplier_messages,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Proposal lists (ticket ai-automation/08): own router.
    if container.supply_chain_proposal_lists is not None:
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import (
            get_form_idempotent_operation,
            get_idempotent_operation,
        )
        from dw_supply_chain.presentation.proposal_list_routes import (
            build_proposal_list_router,
        )

        app.include_router(
            build_proposal_list_router(
                container.supply_chain_proposal_lists,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
                resolve_form_idempotency=get_form_idempotent_operation,
            )
        )

    # Product-development cases (stage 1): their own router on their own guard,
    # as the documents router is.
    if (
        container.supply_chain_propose_product_case is not None
        and container.supply_chain_get_product_case is not None
        and container.supply_chain_list_product_cases is not None
        and container.supply_chain_advance_product_case is not None
        and container.supply_chain_place_order is not None
        and container.supply_chain_reassign_product_case_pic is not None
        and container.supply_chain_list_product_categories is not None
        and container.supply_chain_list_product_case_transitions is not None
        and container.supply_chain_get_product_action_duties is not None
        and container.supply_chain_set_product_action_duties_override is not None
    ):
        from dw_api.dependencies.auth import get_access_context
        from dw_api.dependencies.idempotency import get_idempotent_operation
        from dw_supply_chain.presentation.product_case_routes import build_product_cases_router

        app.include_router(
            build_product_cases_router(
                container.supply_chain_propose_product_case,
                container.supply_chain_get_product_case,
                container.supply_chain_list_product_cases,
                container.supply_chain_advance_product_case,
                container.supply_chain_list_product_case_transitions,
                container.supply_chain_get_product_action_duties,
                container.supply_chain_set_product_action_duties_override,
                place_order=container.supply_chain_place_order,
                reassign_pic=container.supply_chain_reassign_product_case_pic,
                list_categories=container.supply_chain_list_product_categories,
                resolve_access_context=get_access_context,
                resolve_idempotency=get_idempotent_operation,
            )
        )

    # Guard each on the dependency it needs, as the platform routers above do:
    # a router that 500s on every call is worse than an absent one.

    # Development helpers, never mounted in a deployed profile. The dev-token
    # issuer stays gated on dev auth mode so it can never become an OIDC bypass.
    if not settings.is_deployed:
        from dw_api.bootstrap import REPO_ROOT
        from dw_api.routes.v1.dev import build_dev_router

        app.include_router(
            build_dev_router(
                REPO_ROOT,
                settings.dev_secret or "",
                include_session=settings.auth_mode == "dev" and bool(settings.dev_secret),
            ),
            prefix="/api/v1",
        )
    return app


app = create_app()
