"""Org Admin console: workspaces, the role catalog and tenant settings.

Every route resolves the caller's tenant from the verified access context and
the service enforces the ``platform.*`` scope, so an Org Admin only ever reaches
their own tenant. Reads and writes are scoped by RLS underneath.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from dw_api.dependencies.auth import RequireAccessContext
from dw_api.dependencies.services import RequireContainer
from dw_kernel.autonomy import AutonomyLevel
from dw_kernel.errors import InfrastructureError
from dw_platform.application.admin_console import (
    AdminConsoleService,
    ArchiveWorkspace,
    CreateWorkspace,
    RenameWorkspace,
    SetPermissionSets,
    TenantSettings,
    UpdateTenantSettings,
)
from dw_platform.application.cache import membership_cache_pattern, tenant_cache_pattern
from dw_platform.application.hierarchy import HierarchyService, SetManager
from dw_platform.application.separation_of_duties import (
    ConfirmWaiver,
    RevokeWaiver,
    SeparationOfDutiesService,
    SodRuleStatus,
    WaiveRule,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class WorkspaceSummaryView(BaseModel):
    workspace_id: UUID
    slug: str
    name: str
    member_count: int
    archived: bool


class WorkspaceRefView(BaseModel):
    workspace_id: UUID
    slug: str
    name: str


class CreateWorkspaceBody(BaseModel):
    name: str
    slug: str


class RenameWorkspaceBody(BaseModel):
    name: str


class RoleView(BaseModel):
    key: str
    name: str
    scopes: list[str]


class PermissionSetView(BaseModel):
    key: str
    name: str
    scopes: list[str]


class SetPermissionSetsBody(BaseModel):
    permission_set_keys: list[str]


class TenantSettingsView(BaseModel):
    tenant_id: UUID
    slug: str
    name: str
    status: str
    record_visibility: str
    timezone: str | None
    locale: str | None
    max_autonomy_level: AutonomyLevel


class UpdateTenantBody(BaseModel):
    name: str | None = None
    timezone: str | None = None
    locale: str | None = None
    record_visibility: str | None = None
    # The most autonomy any of this tenant's workers may run at. It lowers a
    # worker's declared level and never raises it. Typed as the kernel's level
    # set, so an unknown value is a 422 here and never reaches the service.
    max_autonomy_level: AutonomyLevel | None = None


class HierarchyMemberView(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None
    role_keys: list[str]
    manager_user_id: UUID | None


class SetManagerBody(BaseModel):
    manager_user_id: UUID | None = None


class SodWaiverView(BaseModel):
    reason: str
    granted_by: UUID
    granted_at: datetime
    # Null while the waiver waits for a second admin; it lifts nothing until then.
    confirmed_by: UUID | None
    confirmed_at: datetime | None


class SodRuleView(BaseModel):
    key: str
    description: str
    left_scopes: list[str]
    right_scopes: list[str]
    # False: a floor no tenant can lower, whatever its size.
    waivable: bool
    # This tenant's open waiver of the rule, if it has one.
    waiver: SodWaiverView | None


class WaiverDecisionBody(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def _service(container: RequireContainer) -> AdminConsoleService:
    if container.admin_console is None:
        raise InfrastructureError("database is not configured")
    return container.admin_console


def _hierarchy(container: RequireContainer) -> HierarchyService:
    if container.hierarchy is None:
        raise InfrastructureError("database is not configured")
    return container.hierarchy


def _separation_of_duties(container: RequireContainer) -> SeparationOfDutiesService:
    if container.separation_of_duties is None:
        raise InfrastructureError("database is not configured")
    return container.separation_of_duties


@router.get("/workspaces", response_model=list[WorkspaceSummaryView])
async def list_workspaces(
    context: RequireAccessContext, container: RequireContainer
) -> list[WorkspaceSummaryView]:
    rows = await _service(container).list_workspaces(context)
    return [
        WorkspaceSummaryView(
            workspace_id=w.workspace_id,
            slug=w.slug,
            name=w.name,
            member_count=w.member_count,
            archived=w.archived,
        )
        for w in rows
    ]


logger = logging.getLogger(__name__)


async def _install_report_templates(
    container: object, *, tenant_id: UUID, workspace_id: UUID
) -> None:
    """Cấp ba báo cáo chuẩn cho một không gian làm việc vừa mở.

    Nuốt mọi lỗi có chủ ý: một trục trặc bên CRM không đáng chặn việc mở một
    khách hàng mới (spec 015, R5). Lưới an toàn là
    ``scripts/install_report_templates.py`` — chạy lại lúc nào cũng được và tự
    bù đúng chỗ thiếu.
    """
    install = getattr(container, "install_report_templates", None)
    if install is None:
        return
    try:
        await install(tenant_id, workspace_id)
    except Exception:
        logger.exception(
            "không cài được báo cáo chuẩn cho workspace %s của tenant %s",
            workspace_id,
            tenant_id,
        )


@router.post("/workspaces", response_model=WorkspaceRefView, status_code=201)
async def create_workspace(
    payload: CreateWorkspaceBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> WorkspaceRefView:
    w = await _service(container).create_workspace(
        context, CreateWorkspace(name=payload.name, slug=payload.slug)
    )
    await _install_report_templates(
        container, tenant_id=context.tenant_id, workspace_id=w.workspace_id
    )
    return WorkspaceRefView(workspace_id=w.workspace_id, slug=w.slug, name=w.name)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceRefView)
async def rename_workspace(
    workspace_id: UUID,
    payload: RenameWorkspaceBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> WorkspaceRefView:
    w = await _service(container).rename_workspace(
        context, RenameWorkspace(workspace_id=workspace_id, name=payload.name)
    )
    return WorkspaceRefView(workspace_id=w.workspace_id, slug=w.slug, name=w.name)


@router.post("/workspaces/{workspace_id}/archive", status_code=204)
async def archive_workspace(
    workspace_id: UUID,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _service(container).archive_workspace(
        context, ArchiveWorkspace(workspace_id=workspace_id)
    )
    return Response(status_code=204)


@router.get("/roles", response_model=list[RoleView])
async def list_roles(context: RequireAccessContext, container: RequireContainer) -> list[RoleView]:
    rows = await _service(container).list_roles(context)
    return [RoleView(key=r.key, name=r.name, scopes=list(r.scopes)) for r in rows]


@router.get("/permission-sets", response_model=list[PermissionSetView])
async def list_permission_sets(
    context: RequireAccessContext, container: RequireContainer
) -> list[PermissionSetView]:
    rows = await _service(container).list_permission_sets(context)
    return [PermissionSetView(key=r.key, name=r.name, scopes=list(r.scopes)) for r in rows]


@router.put("/members/{user_id}/permission-sets", status_code=204)
async def set_permission_sets(
    user_id: UUID,
    payload: SetPermissionSetsBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _service(container).set_permission_sets(
        context,
        SetPermissionSets(
            user_id=user_id, permission_set_keys=frozenset(payload.permission_set_keys)
        ),
    )
    # Changed this member's scopes → drop the workspace's cached AccessContexts.
    if container.cache is not None:
        await container.cache.delete_pattern(
            membership_cache_pattern(context.tenant_id, context.workspace_id)
        )
    return Response(status_code=204)


@router.get("/tenant", response_model=TenantSettingsView)
async def get_tenant(
    context: RequireAccessContext, container: RequireContainer
) -> TenantSettingsView:
    return _tenant_view(await _service(container).get_tenant_settings(context))


@router.patch("/tenant", response_model=TenantSettingsView)
async def update_tenant(
    payload: UpdateTenantBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> TenantSettingsView:
    settings = await _service(container).update_tenant_settings(
        context,
        UpdateTenantSettings(
            name=payload.name,
            timezone=payload.timezone,
            locale=payload.locale,
            record_visibility=payload.record_visibility,
            max_autonomy_level=payload.max_autonomy_level,
        ),
    )
    # record_visibility and max_autonomy_level both change what every request in
    # the tenant may do → drop its cached AccessContexts so it takes effect at once.
    if container.cache is not None:
        await container.cache.delete_pattern(tenant_cache_pattern(context.tenant_id))
    return _tenant_view(settings)


@router.get("/hierarchy", response_model=list[HierarchyMemberView])
async def get_hierarchy(
    context: RequireAccessContext, container: RequireContainer
) -> list[HierarchyMemberView]:
    """The org chart: every member of the workspace and who they report to."""
    rows = await _hierarchy(container).get_tree(context)
    return [
        HierarchyMemberView(
            user_id=m.user_id,
            display_name=m.display_name,
            email=m.email,
            role_keys=list(m.role_keys),
            manager_user_id=m.manager_user_id,
        )
        for m in rows
    ]


@router.put("/hierarchy/{user_id}", status_code=204)
async def set_manager(
    user_id: UUID,
    payload: SetManagerBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _hierarchy(container).set_manager(
        context, SetManager(user_id=user_id, manager_user_id=payload.manager_user_id)
    )
    # A manager change reshapes subtrees → the workspace's cached visible_owners
    # are stale; drop them so the new roll-up takes effect at once.
    if container.cache is not None:
        await container.cache.delete_pattern(
            membership_cache_pattern(context.tenant_id, context.workspace_id)
        )
    return Response(status_code=204)


@router.get("/separation-of-duties", response_model=list[SodRuleView])
async def list_separation_of_duties(
    context: RequireAccessContext, container: RequireContainer
) -> list[SodRuleView]:
    rows = await _separation_of_duties(container).list_rules(context)
    return [_rule_view(r) for r in rows]


@router.post("/separation-of-duties/{rule_key}/waiver", status_code=204)
async def waive_separation_of_duties_rule(
    rule_key: str,
    payload: WaiverDecisionBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _separation_of_duties(container).waive(
        context, WaiveRule(rule_key=rule_key, reason=payload.reason)
    )
    return Response(status_code=204)


@router.post("/separation-of-duties/{rule_key}/waiver/revoke", status_code=204)
async def revoke_separation_of_duties_waiver(
    rule_key: str,
    payload: WaiverDecisionBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _separation_of_duties(container).revoke(
        context, RevokeWaiver(rule_key=rule_key, reason=payload.reason)
    )
    return Response(status_code=204)


@router.post("/separation-of-duties/{rule_key}/waiver/confirm", status_code=204)
async def confirm_separation_of_duties_waiver(
    rule_key: str,
    payload: WaiverDecisionBody,
    context: RequireAccessContext,
    container: RequireContainer,
) -> Response:
    await _separation_of_duties(container).confirm(
        context, ConfirmWaiver(rule_key=rule_key, reason=payload.reason)
    )
    return Response(status_code=204)


def _rule_view(r: SodRuleStatus) -> SodRuleView:
    return SodRuleView(
        key=r.key,
        description=r.description,
        left_scopes=list(r.left_scopes),
        right_scopes=list(r.right_scopes),
        waivable=r.waivable,
        waiver=None
        if r.waiver is None
        else SodWaiverView(
            reason=r.waiver.reason,
            granted_by=r.waiver.granted_by,
            granted_at=r.waiver.granted_at,
            confirmed_by=r.waiver.confirmed_by,
            confirmed_at=r.waiver.confirmed_at,
        ),
    )


def _tenant_view(s: TenantSettings) -> TenantSettingsView:
    return TenantSettingsView(
        tenant_id=s.tenant_id,
        slug=s.slug,
        name=s.name,
        status=s.status,
        record_visibility=s.record_visibility,
        timezone=s.timezone,
        locale=s.locale,
        max_autonomy_level=cast(AutonomyLevel, s.max_autonomy_level),
    )
