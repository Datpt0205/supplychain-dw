"""Document drafts: prepare, read, revise, reject, render; and the tenant's own
templates (ADR 0025 point 4; ticket ai-automation/03).

The rules, each decided here where the read or write happens:

- **Who.** `supply_chain.document.read` to read or preview a draft,
  `supply_chain.document.write` to revise or reject one: the same scopes as the
  case's documents, since a draft is a document in waiting. A price field
  (`PRICE_FIELDS`) reads as redacted without `supply_chain.commercial.read`, in
  the reading and in the rendered preview alike, and changing one needs
  `supply_chain.commercial.write`. The tenant's templates are process rules:
  `supply_chain.action_duties.read|write`.
- **Which case.** Read under the caller's tenant (RLS) and required in the
  caller's workspace; a miss of either is not found.
- **What a draft may hold.** Exactly the fields its template declares, each of
  the template's kind; anything else is a 422 naming the field. Gaps are code's:
  a required field with no value, a table row missing a cell.
- **Which version.** A draft id names one version; an edit of it becomes the
  next one, and an edit or a decision of a version that is not the open latest
  one is a 409, as is a second decision. Confirming is not here: it happens only through the step
  approval (ticket 05), in the transaction that adds the document.

`PrepareDocumentDraft` is the lane's door (ticket 05 calls it as the lane): no
route reaches it, and it checks the case like every other handler.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Protocol

from dw_agent_runtime.doc_templates import (
    DocTemplateRegistry,
    DocTemplateSpec,
    DocumentRendererPort,
    LoadedDocTemplate,
    RenderedFile,
    TemplateFieldKind,
    TemplateInspectorPort,
    TemplateValue,
)
from dw_kernel.errors import ConfigError, DomainError, NotFoundError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_platform.domain.audit import AuditEvent
from dw_supply_chain.application.case_documents import CaseLookups
from dw_supply_chain.application.commercial import allows
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    COMMERCIAL_READ,
    COMMERCIAL_WRITE,
    DOCUMENT_READ,
    DOCUMENT_WRITE,
)
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import PRICE_FIELDS
from dw_supply_chain.domain.document_draft import (
    DRAFT_TEMPLATES,
    DocumentDraft,
    DraftDecision,
    DraftSource,
    content_sha256,
    rejection_reason,
)
from dw_supply_chain.domain.extraction import parse_number

_RESOURCE = "document_draft"
_TEMPLATES = "doc_template"
DRAFT_PREPARED = "supply_chain.document_draft.prepared"
DRAFT_REVISED = "supply_chain.document_draft.revised"
DRAFT_REJECTED = "supply_chain.document_draft.rejected"
TEMPLATE_OVERRIDE_SET = "supply_chain.doc_template.override_set"
# What a hidden price reads as in a rendered preview.
HIDDEN = "[đã ẩn]"
_TEXT_MAX = 2000
_ROWS_MAX = 500


# ------------------------------------------------------------- templates ---


@dataclass(frozen=True, slots=True)
class StoredTemplate:
    template_id: str
    version: str
    spec: str
    docx: bytes
    checksum: str


class TemplateOverrideStorePort(Protocol):
    """A tenant's own template versions, read and added under the caller's
    tenant (RLS)."""

    async def get(
        self, context: AccessContext, template_id: str, version: str
    ) -> StoredTemplate | None: ...

    async def list_refs(self, context: AccessContext) -> list[tuple[str, str]]: ...

    async def add(
        self, context: AccessContext, template: StoredTemplate, *, audit: AuditEvent
    ) -> None: ...


@dataclass
class TenantDocTemplates:
    """The platform's templates (loaded from the checkout at start) and each
    tenant's overrides, loaded from storage the first time that tenant asks
    for one. Falls back to the platform, never to another tenant: the store
    is read under the caller's own tenant."""

    registry: DocTemplateRegistry
    overrides: TemplateOverrideStorePort

    async def resolve(
        self, context: AccessContext, template_id: str, version: str
    ) -> LoadedDocTemplate:
        tenant = context.tenant_id
        if not self.registry.has_override(template_id, version, tenant_id=tenant):
            stored = await self.overrides.get(context, template_id, version)
            if stored is not None:
                try:
                    self.registry.load_bytes(
                        stored.spec.encode("utf-8"), stored.docx, tenant_id=tenant
                    )
                except ConfigError:
                    # Another request loaded it first; the stored row is the
                    # same immutable version.
                    if not self.registry.has_override(template_id, version, tenant_id=tenant):
                        raise
        return self.registry.resolve(template_id, version, tenant_id=tenant)


@dataclass(frozen=True, slots=True)
class TemplateListing:
    spec: DocTemplateSpec
    overridden: bool


@dataclass(frozen=True)
class ListDocTemplates:
    registry: DocTemplateRegistry
    overrides: TemplateOverrideStorePort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> list[TemplateListing]:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_TEMPLATES
        )
        own = set(await self.overrides.list_refs(context))
        return [
            TemplateListing(spec=t.spec, overridden=(t.spec.template_id, t.spec.version) in own)
            for t in self.registry.platform_templates()
        ]


@dataclass(frozen=True)
class SetDocTemplateOverride:
    """Stores the tenant's own version of a platform template. The upload is
    checked as the platform's files are (declaration against the document's
    placeholders, no macros), must vary a template the platform ships, and
    must keep its document type: a step's paper stays the same paper."""

    registry: DocTemplateRegistry
    inspector: TemplateInspectorPort
    overrides: TemplateOverrideStorePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, *, spec: bytes, docx: bytes) -> DocTemplateSpec:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_TEMPLATES
        )
        scratch = DocTemplateRegistry(inspector=self.inspector)
        try:
            loaded = scratch.load_bytes(spec, docx)
        except ConfigError as exc:
            raise DomainError("mẫu chứng từ không hợp lệ", details={"error": exc.message}) from exc
        platform = {
            (t.spec.template_id, t.spec.doc_type) for t in self.registry.platform_templates()
        }
        if (loaded.spec.template_id, loaded.spec.doc_type) not in platform:
            raise DomainError(
                "mẫu riêng phải thay một mẫu nền tảng có cùng mã và loại chứng từ",
                details={"template_id": loaded.spec.template_id, "doc_type": loaded.spec.doc_type},
            )
        await self.overrides.add(
            context,
            StoredTemplate(
                template_id=loaded.spec.template_id,
                version=loaded.spec.version,
                spec=spec.decode("utf-8"),
                docx=docx,
                checksum=loaded.checksum,
            ),
            audit=AuditEvent(
                id=self.ids.new_uuid(),
                tenant_id=TenantId(context.tenant_id),
                workspace_id=WorkspaceId(context.workspace_id),
                actor_id=UserId(context.principal_id),
                action=TEMPLATE_OVERRIDE_SET,
                resource_type=_TEMPLATES,
                resource_id=loaded.spec.ref,
                occurred_at=self.clock.now(),
                details={"checksum": loaded.checksum},
            ),
        )
        return loaded.spec


# ----------------------------------------------------------------- fields ---


def _cell(kind: TemplateFieldKind, raw: Any, path: str, errors: list[dict[str, str]]) -> str | None:
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if not isinstance(raw, str | int | float) or isinstance(raw, bool):
        errors.append({"field": path, "error": "not_text"})
        return None
    text = str(raw).strip()
    match kind:
        case TemplateFieldKind.NUMBER:
            number = parse_number(text)
            if number is None:
                errors.append({"field": path, "error": "not_a_number"})
                return None
            return str(number)
        case TemplateFieldKind.DATE:
            try:
                return date.fromisoformat(text).isoformat()
            except ValueError:
                errors.append({"field": path, "error": "not_a_date"})
                return None
        case _:
            if len(text) > _TEXT_MAX:
                errors.append({"field": path, "error": "too_long"})
                return None
            return text


def check_values(spec: DocTemplateSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    """The values a template takes, normalised (numbers canonical, dates ISO),
    or a 422 naming each field that is wrong."""
    errors: list[dict[str, str]] = [
        {"field": name, "error": "unknown_field"} for name in values if spec.field(name) is None
    ]
    clean: dict[str, Any] = {}
    for f in spec.fields:
        if f.name not in values:
            continue
        raw = values[f.name]
        if f.kind is TemplateFieldKind.TABLE:
            if raw is None:
                clean[f.name] = []
                continue
            if not isinstance(raw, list) or len(raw) > _ROWS_MAX:
                errors.append({"field": f.name, "error": "not_a_table"})
                continue
            columns = {c.name: c.kind for c in f.columns or ()}
            rows = []
            for index, row in enumerate(raw):
                if not isinstance(row, Mapping):
                    errors.append({"field": f"{f.name}[{index}]", "error": "not_a_row"})
                    continue
                for name in row:
                    if name not in columns:
                        errors.append(
                            {"field": f"{f.name}[{index}].{name}", "error": "unknown_field"}
                        )
                rows.append(
                    {
                        name: _cell(kind, row.get(name), f"{f.name}[{index}].{name}", errors)
                        for name, kind in columns.items()
                    }
                )
            clean[f.name] = rows
        else:
            clean[f.name] = _cell(f.kind, raw, f.name, errors)
    if errors:
        raise DomainError("bản nháp không khớp mẫu chứng từ", details={"errors": errors})
    return clean


def compute_gaps(spec: DocTemplateSpec, fields: Mapping[str, Any]) -> list[str]:
    """Required fields with no value, and table cells left empty."""
    gaps: list[str] = []
    for f in spec.fields:
        entry = fields.get(f.name)
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if f.kind is TemplateFieldKind.TABLE:
            rows = value if isinstance(value, list) else []
            if f.required and not rows:
                gaps.append(f.name)
            for index, row in enumerate(rows):
                for column in f.columns or ():
                    if not (isinstance(row, Mapping) and row.get(column.name)):
                        gaps.append(f"{f.name}[{index}].{column.name}")
        elif f.required and value in (None, ""):
            gaps.append(f.name)
    return gaps


def _is_price(name: str) -> bool:
    return name in PRICE_FIELDS


def without_prices(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Every price value emptied and marked, a table's price cells too."""
    out: dict[str, Any] = {}
    for name, entry in fields.items():
        if not isinstance(entry, Mapping):
            continue
        value = entry.get("value")
        if _is_price(name):
            out[name] = {"value": None, "source": None, "redacted": True}
        elif isinstance(value, list):
            out[name] = {
                **entry,
                "value": [
                    {k: (None if _is_price(k) else v) for k, v in row.items()}
                    if isinstance(row, Mapping)
                    else row
                    for row in value
                ],
                "redacted_columns": sorted(
                    {k for row in value if isinstance(row, Mapping) for k in row if _is_price(k)}
                ),
            }
        else:
            out[name] = dict(entry)
    return out


def _touches_prices(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    for name in set(before) | set(after):
        old = before.get(name, {}).get("value") if isinstance(before.get(name), Mapping) else None
        new = after.get(name, {}).get("value") if isinstance(after.get(name), Mapping) else None
        if _is_price(name) and old != new:
            return True
        if isinstance(old, list) or isinstance(new, list):
            old_rows = old if isinstance(old, list) else []
            new_rows = new if isinstance(new, list) else []
            if len(old_rows) != len(new_rows):
                if any(_is_price(k) and v for row in new_rows for k, v in row.items()):
                    return True
                continue
            for a, b in zip(old_rows, new_rows, strict=True):
                if any(_is_price(k) and a.get(k) != b.get(k) for k in set(a) | set(b)):
                    return True
    return False


# ------------------------------------------------------------------ ports --


@dataclass(frozen=True, slots=True)
class NewDocumentDraft:
    id: uuid.UUID
    lineage_id: uuid.UUID
    version: int
    case_kind: CaseKind
    case_id: uuid.UUID
    doc_type: DocumentType
    template_id: str
    template_version: str
    prompt_id: str | None
    prompt_version: str | None
    fields: dict[str, Any]
    gaps: list[str]
    sources: list[dict[str, str | None]]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class NewDraftDecision:
    id: uuid.UUID
    draft_id: uuid.UUID
    decision: DraftDecision
    reason: str | None


class DocumentDraftRepositoryPort(Protocol):
    async def add(
        self, context: AccessContext, draft: NewDocumentDraft, *, audit: AuditEvent
    ) -> DocumentDraft:
        """Raises `ConflictError` when the lineage already has this version."""
        ...

    async def get(self, context: AccessContext, draft_id: uuid.UUID) -> DocumentDraft | None: ...

    async def latest_for_case(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[DocumentDraft]: ...

    async def decide(
        self, context: AccessContext, decision: NewDraftDecision, *, audit: AuditEvent
    ) -> None:
        """Raises `ConflictError` when the version already has a decision."""
        ...


class DraftTemplatesPort(Protocol):
    async def resolve(
        self, context: AccessContext, template_id: str, version: str
    ) -> LoadedDocTemplate: ...


async def _require_case(
    cases: CaseLookups, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
) -> None:
    workspace = await cases[case_kind].case_workspace(context, case_id)
    if workspace is None or workspace != context.workspace_id:
        raise NotFoundError(
            "case not found", details={"case_kind": case_kind.value, "case_id": str(case_id)}
        )


def _audit(
    context: AccessContext,
    ids: IdGenerator,
    clock: UtcClock,
    action: str,
    draft_id: uuid.UUID,
    details: dict[str, Any],
) -> AuditEvent:
    return AuditEvent(
        id=ids.new_uuid(),
        tenant_id=TenantId(context.tenant_id),
        workspace_id=WorkspaceId(context.workspace_id),
        actor_id=UserId(context.principal_id),
        action=action,
        resource_type=_RESOURCE,
        resource_id=str(draft_id),
        occurred_at=clock.now(),
        details=details,
    )


# --------------------------------------------------------------- handlers --


@dataclass(frozen=True, slots=True)
class FieldInput:
    """A value for a draft field and where it came from: a document and the
    quote read in it, words a model wrote that checked out against the
    evidence they cite (`cites`), or nothing (a value code computed)."""

    value: Any
    document_id: uuid.UUID | None = None
    quote: str | None = None
    cites: tuple[str, ...] = ()

    def source(self) -> dict[str, Any] | None:
        """A document and its quote; words a model wrote (or read) that checked
        out, with what they cite; both when a model read a document's quote."""
        out: dict[str, Any] = {}
        if self.document_id is not None:
            out = {"document_id": str(self.document_id), "quote": self.quote}
        if self.cites:
            out |= {"ai_written": True, "cites": list(self.cites)}
            if self.quote is not None:
                out["quote"] = self.quote
        return out or None


@dataclass(frozen=True)
class PrepareDocumentDraft:
    """A new draft (version 1 of a new lineage) for a case. The lane's door:
    no route calls it."""

    cases: CaseLookups
    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        *,
        case_kind: CaseKind,
        case_id: uuid.UUID,
        doc_type: DocumentType,
        values: Mapping[str, FieldInput],
        sources: Sequence[DraftSource] = (),
        prompt: tuple[str, str] | None = None,
    ) -> DocumentDraft:
        await _require_case(self.cases, context, case_kind, case_id)
        ref = DRAFT_TEMPLATES.get(doc_type)
        if ref is None:
            raise DomainError("loại chứng từ này chưa có mẫu", details={"doc_type": doc_type.value})
        template = await self.templates.resolve(context, *ref)
        clean = check_values(template.spec, {n: v.value for n, v in values.items()})
        fields = {
            name: {"value": value, "source": values[name].source()} for name, value in clean.items()
        }
        draft_id = self.ids.new_uuid()
        return await self.drafts.add(
            context,
            NewDocumentDraft(
                id=draft_id,
                lineage_id=draft_id,
                version=1,
                case_kind=case_kind,
                case_id=case_id,
                doc_type=doc_type,
                template_id=template.spec.template_id,
                template_version=template.spec.version,
                prompt_id=None if prompt is None else prompt[0],
                prompt_version=None if prompt is None else prompt[1],
                fields=fields,
                gaps=compute_gaps(template.spec, fields),
                sources=[s.as_json() for s in sources],
                content_sha256=content_sha256(doc_type, template.spec.ref, fields),
            ),
            audit=_audit(
                context,
                self.ids,
                self.clock,
                DRAFT_PREPARED,
                draft_id,
                {"case_kind": case_kind.value, "case_id": str(case_id), "doc_type": doc_type.value},
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftReading:
    draft: DocumentDraft
    spec: DocTemplateSpec
    prices_visible: bool
    can_edit: bool


@dataclass(frozen=True)
class _DraftReader:
    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    authz: AuthorizationPort

    async def read(self, context: AccessContext, draft: DocumentDraft) -> DraftReading:
        visible = await allows(self.authz, context, COMMERCIAL_READ, _RESOURCE)
        template = await self.templates.resolve(context, draft.template_id, draft.template_version)
        return DraftReading(
            draft=draft if visible else replace(draft, fields=without_prices(draft.fields)),
            spec=template.spec,
            prices_visible=visible,
            can_edit=await allows(self.authz, context, DOCUMENT_WRITE, _RESOURCE),
        )

    async def get(self, context: AccessContext, draft_id: uuid.UUID) -> DocumentDraft:
        draft = await self.drafts.get(context, draft_id)
        if draft is None or draft.workspace_id != context.workspace_id:
            raise NotFoundError("draft not found", details={"draft_id": str(draft_id)})
        return draft


@dataclass(frozen=True)
class ListCaseDrafts:
    cases: CaseLookups
    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    authz: AuthorizationPort

    async def handle(
        self, context: AccessContext, case_kind: CaseKind, case_id: uuid.UUID
    ) -> list[DraftReading]:
        await self.authz.require(
            context=context, action=DOCUMENT_READ, resource_type=_RESOURCE, resource_id=str(case_id)
        )
        await _require_case(self.cases, context, case_kind, case_id)
        reader = _DraftReader(self.drafts, self.templates, self.authz)
        return [
            await reader.read(context, d)
            for d in await self.drafts.latest_for_case(context, case_kind, case_id)
        ]


@dataclass(frozen=True)
class GetDocumentDraft:
    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, draft_id: uuid.UUID) -> DraftReading:
        await self.authz.require(
            context=context,
            action=DOCUMENT_READ,
            resource_type=_RESOURCE,
            resource_id=str(draft_id),
        )
        reader = _DraftReader(self.drafts, self.templates, self.authz)
        return await reader.read(context, await reader.get(context, draft_id))


@dataclass(frozen=True)
class ReviseDocumentDraft:
    """A person's edit: the named fields replaced, the rest kept, as the next
    version of the same draft."""

    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self,
        context: AccessContext,
        draft_id: uuid.UUID,
        *,
        values: Mapping[str, Any],
    ) -> DocumentDraft:
        await self.authz.require(
            context=context,
            action=DOCUMENT_WRITE,
            resource_type=_RESOURCE,
            resource_id=str(draft_id),
        )
        reader = _DraftReader(self.drafts, self.templates, self.authz)
        # A draft id names one version; it is edited only while it is the open
        # latest one. Two edits of it race for the next version, and the
        # lineage's UNIQUE gives the second a 409.
        current = await reader.get(context, draft_id)
        current.require_open()
        template = await self.templates.resolve(
            context, current.template_id, current.template_version
        )
        clean = check_values(template.spec, values)
        edited_by = {"edited_by": str(context.principal_id)}
        fields = dict(current.fields)
        for name, value in clean.items():
            fields[name] = {"value": value, "source": edited_by}
        if _touches_prices(current.fields, fields):
            await self.authz.require(
                context=context,
                action=COMMERCIAL_WRITE,
                resource_type=_RESOURCE,
                resource_id=str(draft_id),
            )
        new_id = self.ids.new_uuid()
        return await self.drafts.add(
            context,
            NewDocumentDraft(
                id=new_id,
                lineage_id=current.lineage_id,
                version=current.version + 1,
                case_kind=current.case_kind,
                case_id=current.case_id,
                doc_type=current.doc_type,
                template_id=current.template_id,
                template_version=current.template_version,
                prompt_id=current.prompt_id,
                prompt_version=current.prompt_version,
                fields=fields,
                gaps=compute_gaps(template.spec, fields),
                sources=[s.as_json() for s in current.sources],
                content_sha256=content_sha256(current.doc_type, template.spec.ref, fields),
            ),
            audit=_audit(
                context,
                self.ids,
                self.clock,
                DRAFT_REVISED,
                new_id,
                {
                    "lineage_id": str(current.lineage_id),
                    "from_version": current.version,
                    "fields": sorted(clean),
                },
            ),
        )


@dataclass(frozen=True)
class RejectDocumentDraft:
    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(
        self, context: AccessContext, draft_id: uuid.UUID, *, reason: str | None
    ) -> DocumentDraft:
        await self.authz.require(
            context=context,
            action=DOCUMENT_WRITE,
            resource_type=_RESOURCE,
            resource_id=str(draft_id),
        )
        reader = _DraftReader(self.drafts, self.templates, self.authz)
        current = await reader.get(context, draft_id)
        current.require_open()
        text = rejection_reason(reason)
        await self.drafts.decide(
            context,
            NewDraftDecision(
                id=self.ids.new_uuid(),
                draft_id=draft_id,
                decision=DraftDecision.REJECTED,
                reason=text,
            ),
            audit=_audit(
                context,
                self.ids,
                self.clock,
                DRAFT_REJECTED,
                draft_id,
                {"lineage_id": str(current.lineage_id), "version": current.version},
            ),
        )
        return replace(current, decision=DraftDecision.REJECTED, decision_reason=text)


@dataclass(frozen=True, slots=True)
class RenderedDraft:
    file: RenderedFile
    filename: str


def template_values(fields: Mapping[str, Any], *, hide_prices: bool) -> dict[str, TemplateValue]:
    """A draft's fields as the renderer takes them; a hidden price prints as
    `HIDDEN`, never as a number and never as a gap."""
    values: dict[str, TemplateValue] = {}
    for name, entry in fields.items():
        value = entry.get("value") if isinstance(entry, Mapping) else None
        if isinstance(value, list):
            values[name] = [
                {
                    k: (HIDDEN if hide_prices and _is_price(k) and v is not None else v)
                    for k, v in row.items()
                }
                for row in value
                if isinstance(row, Mapping)
            ]
        elif hide_prices and _is_price(name) and value is not None:
            values[name] = HIDDEN
        else:
            values[name] = value if isinstance(value, str) else None
    return values


@dataclass(frozen=True)
class RenderDocumentDraft:
    """The draft as its template prints it, for a preview."""

    drafts: DocumentDraftRepositoryPort
    templates: DraftTemplatesPort
    renderer: DocumentRendererPort
    authz: AuthorizationPort

    async def handle(self, context: AccessContext, draft_id: uuid.UUID) -> RenderedDraft:
        await self.authz.require(
            context=context,
            action=DOCUMENT_READ,
            resource_type=_RESOURCE,
            resource_id=str(draft_id),
        )
        reader = _DraftReader(self.drafts, self.templates, self.authz)
        draft = await reader.get(context, draft_id)
        visible = await allows(self.authz, context, COMMERCIAL_READ, _RESOURCE)
        template = await self.templates.resolve(context, draft.template_id, draft.template_version)
        rendered = self.renderer.render(
            template, template_values(draft.fields, hide_prices=not visible)
        )
        return RenderedDraft(
            file=rendered,
            filename=f"{template.spec.title} - ban nhap v{draft.version}.{rendered.extension}",
        )


__all__ = [
    "DraftReading",
    "FieldInput",
    "GetDocumentDraft",
    "ListCaseDrafts",
    "ListDocTemplates",
    "PrepareDocumentDraft",
    "RejectDocumentDraft",
    "RenderDocumentDraft",
    "ReviseDocumentDraft",
    "SetDocTemplateOverride",
    "TenantDocTemplates",
    "check_values",
    "compute_gaps",
]
