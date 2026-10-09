"""Step 7: the BM04 AI prefills, and the profile version its approval makes
(ticket ai-automation/11; ADR 0026 E15, ADR 0025).

What preparation reads (`Bm04Preparation.facts`), all under the lane's tenant
AND workspace:

- the case's own facts (code, product name);
- the step's source readings as the extraction lane kept them: the supplier's
  quotation or specification (`supplier_quotation`) and a BM04 the supplier
  filled (`product_profile_bm04`), each field with its document and quote. The
  unit price is the quotation line's: the only line, or the one line naming the
  product; several lines and none naming it is no price, and a finding;
- the approved sample evaluation record (the newest confirmed one).

Code reconciles them (`domain.bm04_prefill.reconcile`): one value is kept with
its source, two different values are a conflict and an empty field. Then ONE
structured call (`draft_bm04@1.0.0`, skill `bm04_guide`) may fill what is still
empty and not commercial, from evidence that carries no price; each value it
keeps cites one item and a quote code finds there.

On approval (`Bm04ProfileWriter.profile_for`), the confirmed BM04 becomes a new
`product_profiles` version in the same transaction as the step: the attributes
the tenant's schema names, MOQ, lead time and Incoterm; the price and currency
only when the decider holds `supply_chain.commercial.write`, the previous
version's otherwise (`ProfileCommercial.with_prices_of`, AI-01's rule). A value
the schema refuses is left out and named in the audit; a required field still
missing writes no version at all (the BM04 card shows it empty, a person
fills it): a partial profile is recoverable, a wrong one is not.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.doc_templates import DocTemplateSpec, TemplateFieldKind
from dw_agent_runtime.model.budget import BudgetExceededError
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.errors import DomainError, InfrastructureError, QuotaExceededError
from dw_kernel.ports import IdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_supply_chain.application.commercial import allows
from dw_supply_chain.application.handlers import COMMERCIAL_WRITE
from dw_supply_chain.application.ports import TenantPlanPort
from dw_supply_chain.bm04_schema import Bm04FieldKind, SupplyChainBm04Schema
from dw_supply_chain.domain.bm04_prefill import (
    CASE_FIELDS,
    COMMERCIAL_FIELDS,
    Bm04Candidate,
    Bm04Reconciled,
    Bm04Source,
    Bm04Writing,
    ground_bm04_writing,
    mask_prices,
    reconcile,
)
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.commercial import (
    PRICE_FIELDS,
    Incoterm,
    NewProductProfile,
    ProductProfile,
    ProfileCommercial,
)
from dw_supply_chain.domain.document_draft import DocumentDraft, DraftStatus, field_value
from dw_supply_chain.domain.extraction import normalize, parse_quantity
from dw_supply_chain.domain.grounded_writing import EvidenceItem
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.workflows.grounded_writing import WritingPrompt, write_with_evidence

logger = logging.getLogger(__name__)

BM04_PROMPT = WritingPrompt("supply_chain.draft_bm04", "1.0.0")
SOURCE_LABELS: Mapping[DocumentType, str] = {
    DocumentType.SUPPLIER_QUOTATION: "Báo giá/spec của NCC",
    DocumentType.PRODUCT_PROFILE_BM04: "BM04 do NCC gửi",
}
# Never shown to the model: prices, and the lines and totals that carry them.
_HIDDEN = PRICE_FIELDS | {"total", "lines"}


class Bm04SchemaPort(Protocol):
    async def resolve(self, context: AccessContext) -> SupplyChainBm04Schema: ...


class Bm04ProfileReadPort(Protocol):
    async def latest(self, context: AccessContext, case_id: uuid.UUID) -> ProductProfile | None: ...


class Bm04WriterPort(Protocol):
    """The model's BM04 fields, or None when there are none to be had."""

    async def write(
        self,
        context: AccessContext,
        *,
        case_id: uuid.UUID,
        fields: Sequence[Mapping[str, str]],
        evidence: Sequence[EvidenceItem],
    ) -> Bm04Writing | None: ...


@dataclass(frozen=True, slots=True)
class Bm04Reading:
    """A source document's kept fields, as preparation hands them over."""

    document_id: uuid.UUID
    doc_type: DocumentType
    fields: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Bm04Facts:
    """What the BM04 recipe and the `bm04_sources` check read."""

    reconciled: Bm04Reconciled
    labels: Mapping[str, str]
    schema: SupplyChainBm04Schema
    # Every unit price a source wrote: masked wherever a quote is shown.
    prices: tuple[Decimal, ...]
    # The quotation has several priced lines and none names the product.
    lines_ambiguous: bool
    written: bool = False
    dropped: int = 0


@dataclass(frozen=True)
class Bm04Writer:
    """Implements `Bm04WriterPort`: one structured call through the process's
    one-call gateway. A refused or failed call is no writing: the draft keeps
    code's values and its gaps."""

    gateway: ModelGateway
    plans: TenantPlanPort
    ids: IdGenerator
    worker_id: str
    worker_version: str
    model_profile: str | None = None

    async def write(
        self,
        context: AccessContext,
        *,
        case_id: uuid.UUID,
        fields: Sequence[Mapping[str, str]],
        evidence: Sequence[EvidenceItem],
    ) -> Bm04Writing | None:
        plan = await self.plans.plan_of(context.tenant_id)
        if plan is None:
            return None
        run_id = self.ids.new_uuid()
        try:
            return await write_with_evidence(
                self.gateway,
                RunContext(
                    run_id=run_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.principal_id,
                    worker_id=self.worker_id,
                    worker_version=self.worker_version,
                    channel="worker",
                    plan_id=plan,
                    roles=frozenset(),
                    scopes=frozenset(),
                    trace_id=str(run_id),
                    subject_ref=f"product_dev_case:{case_id}",
                ),
                BM04_PROMPT,
                Bm04Writing,
                task={"purpose": "bm04", "fields": list(fields)},
                evidence=evidence,
                model_profile=self.model_profile,
            )
        except (
            QuotaExceededError,
            ModelOutputInvalidError,
            InfrastructureError,
            BudgetExceededError,
        ) as exc:
            logger.warning("bm04: no model writing (%s)", type(exc).__name__)
            return None


def _number_fields(spec: DocTemplateSpec) -> frozenset[str]:
    return frozenset(f.name for f in spec.fields if f.kind is TemplateFieldKind.NUMBER)


def _entry(fields: Mapping[str, Any], name: str) -> tuple[str, str | None] | None:
    entry = fields.get(name)
    if isinstance(entry, Mapping) and isinstance(entry.get("value"), str) and entry["value"]:
        quote = entry.get("quote")
        return entry["value"], quote if isinstance(quote, str) else None
    return None


def _price_line(reading: Bm04Reading, product: str) -> tuple[tuple[str, str | None] | None, bool]:
    """The quotation's unit price for this product, and whether lines made it
    ambiguous: the only priced line, or the one priced line naming it."""
    lines = reading.fields.get("lines")
    priced = [
        line
        for line in (lines if isinstance(lines, list) else [])
        if isinstance(line, Mapping) and _entry(line, "unit_price") is not None
    ]
    if len(priced) == 1:
        return _entry(priced[0], "unit_price"), False
    naming = [
        line
        for line in priced
        if (d := _entry(line, "description")) is not None and normalize(product) in normalize(d[0])
    ]
    if len(naming) == 1:
        return _entry(naming[0], "unit_price"), False
    return None, len(priced) > 1


def approved_evaluation(drafts: Sequence[DocumentDraft]) -> DocumentDraft | None:
    confirmed = [
        d
        for d in drafts
        if d.doc_type is DocumentType.SAMPLE_EVALUATION and d.status is DraftStatus.CONFIRMED
    ]
    return max(confirmed, key=lambda d: d.created_at) if confirmed else None


@dataclass(frozen=True)
class Bm04Preparation:
    """What the BM04 recipe and check read: the tenant's schema and the
    model that fills what code could not."""

    schemas: Bm04SchemaPort
    writer: Bm04WriterPort | None = None
    # The case's BM04 versions (`product_profiles`): what step 8 compares
    # the supplier's reply with (ticket ai-automation/12).
    profiles: Bm04ProfileReadPort | None = None

    async def facts(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        spec: DocTemplateSpec,
        readings: Sequence[Bm04Reading],
    ) -> Bm04Facts:
        """Code's candidates reconciled; no model yet."""
        names = {f.name for f in spec.fields if f.kind is not TemplateFieldKind.TABLE}
        case_source = Bm04Source("case", "Hồ sơ")
        candidates = [
            Bm04Candidate(name, value, case_source)
            for name, value in (
                ("proposal_code", case.proposal_code),
                ("product_name", case.product_name),
            )
            if name in names and value
        ]
        prices: list[Decimal] = []
        ambiguous = False
        for reading in readings:
            label = SOURCE_LABELS.get(reading.doc_type, reading.doc_type.value)
            source = Bm04Source(f"doc:{reading.document_id}", label, reading.document_id)
            for name in sorted(names - CASE_FIELDS):
                found = _entry(reading.fields, name)
                if name == "unit_price" and reading.doc_type is DocumentType.SUPPLIER_QUOTATION:
                    found, lines_ambiguous = _price_line(reading, case.product_name)
                    ambiguous = ambiguous or lines_ambiguous
                    found = found or _entry(reading.fields, name)
                if found is None:
                    continue
                if name == "unit_price" and (p := parse_quantity(found[0])) is not None:
                    prices.append(p)
                candidates.append(Bm04Candidate(name, found[0], source, found[1]))
        return Bm04Facts(
            reconciled=reconcile(candidates, _number_fields(spec)),
            labels={f.name: f.label for f in spec.fields},
            schema=await self.schemas.resolve(context),
            prices=tuple(prices),
            lines_ambiguous=ambiguous,
        )

    async def written(
        self,
        context: AccessContext,
        case: ProductDevelopmentCase,
        spec: DocTemplateSpec,
        readings: Sequence[Bm04Reading],
        evaluation: DocumentDraft | None,
        facts: Bm04Facts,
    ) -> Bm04Facts:
        """The facts with what the model filled that checks out, or as they
        were when no writer is wired, nothing is left to fill, or it gave
        nothing."""
        conflicted = {c.field for c in facts.reconciled.conflicts}
        fillable = frozenset(
            f.name
            for f in spec.fields
            if f.kind is not TemplateFieldKind.TABLE
            and f.name not in facts.reconciled.kept
            and f.name not in conflicted
            and f.name not in COMMERCIAL_FIELDS | CASE_FIELDS | PRICE_FIELDS
        )
        if self.writer is None or not fillable:
            return facts
        evidence, sources = bm04_evidence(case, readings, evaluation, facts.prices)
        writing = await self.writer.write(
            context,
            case_id=case.id.value,
            fields=[
                {"name": f.name, "label": f.label, "kind": f.kind.value}
                for f in spec.fields
                if f.name in fillable
            ],
            evidence=evidence,
        )
        if writing is None:
            return facts
        kept, dropped = ground_bm04_writing(
            writing, evidence, fillable, _number_fields(spec), sources
        )
        merged = dict(facts.reconciled.kept)
        merged.update({c.field: c for c in kept})
        return Bm04Facts(
            reconciled=Bm04Reconciled(kept=merged, conflicts=facts.reconciled.conflicts),
            labels=facts.labels,
            schema=facts.schema,
            prices=facts.prices,
            lines_ambiguous=facts.lines_ambiguous,
            written=True,
            dropped=dropped,
        )


def bm04_evidence(
    case: ProductDevelopmentCase,
    readings: Sequence[Bm04Reading],
    evaluation: DocumentDraft | None,
    prices: Sequence[Decimal],
) -> tuple[list[EvidenceItem], dict[str, Bm04Source]]:
    """What the model is shown, each item a key it may cite: the case, each
    source's kept fields and its line descriptions, never a price (left out by
    name, and masked anywhere else code knows it), and the approved record."""
    sources = {"case": Bm04Source("case", "Hồ sơ")}
    items = [
        EvidenceItem(
            "case",
            "Hồ sơ",
            f"Mã đề xuất: {case.proposal_code}; Sản phẩm: {case.product_name}; "
            f"Nhóm: {case.category}; NCC: {case.supplier_name or 'chưa có'}",
        )
    ]
    for reading in readings:
        label = SOURCE_LABELS.get(reading.doc_type, reading.doc_type.value)
        key = f"doc:{reading.document_id}"
        parts = [
            f"{name}: {found[1] or found[0]}"
            for name in sorted(reading.fields)
            if name not in _HIDDEN and (found := _entry(reading.fields, name)) is not None
        ]
        lines = reading.fields.get("lines")
        for line in lines if isinstance(lines, list) else []:
            if isinstance(line, Mapping) and (d := _entry(line, "description")) is not None:
                parts.append(f"dòng hàng: {d[0]}")
        if parts:
            sources[key] = Bm04Source(key, label, reading.document_id)
            items.append(EvidenceItem(key, label, mask_prices("; ".join(parts), prices)))
    if evaluation is not None:
        key = f"draft:{evaluation.id}"
        rows = field_value(evaluation.fields, "criteria")
        lines = [
            f"{r.get('criterion')}: chuẩn {r.get('standard')}, đo {r.get('measured')}, "
            f"{r.get('result')}"
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, Mapping)
        ]
        conclusion = field_value(evaluation.fields, "conclusion")
        sources[key] = Bm04Source(key, "Biên bản đánh giá mẫu đã duyệt")
        items.append(
            EvidenceItem(
                key,
                "Biên bản đánh giá mẫu đã duyệt",
                mask_prices(f"Kết luận: {conclusion}; " + "; ".join(lines), prices),
            )
        )
    return items, sources


# ------------------------------------------------------------ on approval --


def _attribute(kind: Bm04FieldKind, raw: str, options: Sequence[str] | None) -> Any:
    text = raw.strip()
    match kind:
        case Bm04FieldKind.TEXT:
            return text
        case Bm04FieldKind.NUMBER:
            number = parse_quantity(text)
            return None if number is None else float(number)
        case Bm04FieldKind.INTEGER:
            number = parse_quantity(text)
            return int(number) if number is not None and number == int(number) else None
        case Bm04FieldKind.CHOICE:
            return text if text in (options or ()) else None
        case Bm04FieldKind.BOOLEAN:
            return {"có": True, "yes": True, "không": False, "no": False}.get(text.casefold())


def _int(raw: str | None) -> int | None:
    number = None if raw is None else parse_quantity(raw)
    return int(number) if number is not None and number == int(number) else None


@dataclass(frozen=True)
class Bm04Profile:
    """A profile version to write with the step, and what was left out."""

    profile: NewProductProfile | None
    skipped: tuple[str, ...] = field(default=())
    prices_set: bool = False


@dataclass(frozen=True)
class Bm04ProfileWriter:
    schemas: Bm04SchemaPort
    profiles: Bm04ProfileReadPort
    authz: AuthorizationPort
    ids: IdGenerator

    async def profile_for(
        self, context: AccessContext, case_id: uuid.UUID, fields: Mapping[str, Any]
    ) -> Bm04Profile:
        """`context` is the decider's."""

        def text(name: str) -> str | None:
            value = field_value(fields, name)
            return value.strip() if isinstance(value, str) and value.strip() else None

        schema = await self.schemas.resolve(context)
        skipped: list[str] = []
        attributes: dict[str, Any] = {}
        for spec in schema.fields:
            raw = text(spec.key)
            if raw is None:
                continue
            value = _attribute(spec.kind, raw, spec.options)
            if value is None:
                skipped.append(spec.key)
                continue
            attributes[spec.key] = value
        try:
            attributes = schema.check(attributes)
        except DomainError as refusal:
            listed: object = refusal.details.get("errors") if refusal.details else None
            errors: list[Mapping[str, Any]] = (
                [e for e in listed if isinstance(e, Mapping)] if isinstance(listed, list) else []
            )
            return Bm04Profile(None, tuple(skipped + [str(e.get("field")) for e in errors]))
        incoterm_raw = text("incoterm")
        incoterm = (
            Incoterm(incoterm_raw.upper())
            if incoterm_raw and incoterm_raw.upper() in Incoterm.__members__
            else None
        )
        if incoterm_raw and incoterm is None:
            skipped.append("incoterm")
        try:
            planning = ProfileCommercial.of(
                unit_price=None,
                currency=None,
                moq=_int(text("moq")),
                lead_time_days=_int(text("lead_time_days")),
                incoterm=incoterm,
            )
        except DomainError:
            planning = ProfileCommercial.of(
                unit_price=None, currency=None, moq=None, lead_time_days=None, incoterm=incoterm
            )
            skipped.extend(["moq", "lead_time_days"])
        prices: ProfileCommercial | None = None
        amount = text("unit_price")
        # A BM04 without a price keeps the previous version's; one with a
        # price writes it only for a decider who may set prices.
        if amount is not None and await allows(
            self.authz, context, COMMERCIAL_WRITE, "product_profile"
        ):
            try:
                prices = ProfileCommercial.of(
                    unit_price=Decimal(amount),
                    currency=text("currency"),
                    moq=None,
                    lead_time_days=None,
                    incoterm=None,
                )
            except (DomainError, InvalidOperation):
                skipped.extend(["unit_price", "currency"])
        if prices is None:
            previous = await self.profiles.latest(context, case_id)
            commercial = planning.with_prices_of(None if previous is None else previous.commercial)
        else:
            commercial = planning.with_prices_of(prices)
        return Bm04Profile(
            NewProductProfile(
                id=self.ids.new_uuid(),
                product_dev_case_id=case_id,
                commercial=commercial,
                attributes=attributes,
                schema_version=schema.policy_version,
            ),
            tuple(skipped),
            prices_set=prices is not None,
        )


__all__ = [
    "BM04_PROMPT",
    "Bm04Facts",
    "Bm04Preparation",
    "Bm04Profile",
    "Bm04ProfileReadPort",
    "Bm04ProfileWriter",
    "Bm04Reading",
    "Bm04SchemaPort",
    "Bm04Writer",
    "Bm04WriterPort",
    "approved_evaluation",
    "bm04_evidence",
]
