"""The eval grader of step 10's PO draft (ticket ai-automation/14),
`supply_chain.purchase_order`.

It runs the REAL `PreparePurchaseOrders`, `GetPurchaseOrderProposal` and
`ApprovePurchaseOrder` over the in-memory world of `testing.purchase_orders`
(stores that keep RLS, the decisions' UNIQUE, the case's version and the
tenant's PO numbers), with the shipped templates and the real renderer. No
model is involved at all: everything graded is what code guarantees.

A case gives the PO case (`quantities`, possibly of another tenant:
`case_in`), the product's BM04 (`profile`, possibly saved in another
workspace: `profile_in`), the supplier's confirmation read at step 8
(`confirmation`, possibly read in another workspace: `confirmation_in`), the
terms a person set on the case (`terms`), a person's edit of the draft
(`edit`) and a decision (`decide`: `scopes`, `po_reference`). Expected:
`drafts`, `draft_fields` (value by name; `lines` as `[sku, qty, price,
total]` rows), `findings` (codes that must be named), `decision`
(`po_created` or `refused`), `case_state`, and `must_not_contain` (text no
notification and no finding may hold: a price).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date
from typing import Any

from dw_evals.graders import GraderContext, GradeResult
from dw_kernel.errors import ConflictError, DomainError, PermissionDeniedError
from dw_supply_chain.application.document_drafts import NewDocumentDraft
from dw_supply_chain.application.handlers import COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.commercial import Incoterm
from dw_supply_chain.domain.document_draft import content_sha256
from dw_supply_chain.testing.purchase_orders import ORDERING, POWorld

_ALL_SCOPES = [ORDERING, COMMERCIAL_READ, COMMERCIAL_WRITE, PO_CASE_READ]


def _edit(world: POWorld, draft: Any, edit: dict[str, Any]) -> Any:
    fields = json.loads(json.dumps(draft.fields))
    for name, value in edit.items():
        fields[name] = {"value": value, "source": {"edited_by": "eval"}}
    new_id = uuid.uuid4()
    world.drafts.insert(
        world.context(),
        NewDocumentDraft(
            id=new_id,
            lineage_id=draft.lineage_id,
            version=draft.version + 1,
            case_kind=draft.case_kind,
            case_id=draft.case_id,
            doc_type=draft.doc_type,
            template_id=draft.template_id,
            template_version=draft.template_version,
            prompt_id=None,
            prompt_version=None,
            fields=fields,
            gaps=[],
            sources=[],
            content_sha256=content_sha256(
                draft.doc_type, f"{draft.template_id}@{draft.template_version}", fields
            ),
        ),
    )
    return new_id


async def _run(input_data: dict[str, Any]) -> dict[str, Any]:
    world = POWorld()
    tenant = uuid.uuid4() if input_data.get("case_in") == "other_tenant" else None
    case = world.add_case(quantities=input_data.get("quantities", [500, 1200]), tenant=tenant)
    elsewhere = uuid.uuid4()
    if input_data.get("profile") is not None:
        spec = input_data["profile"]
        world.add_profile(
            case,
            unit_price=spec.get("unit_price"),
            currency=spec.get("currency"),
            incoterm=None if spec.get("incoterm") is None else Incoterm(spec["incoterm"]),
            workspace=elsewhere if input_data.get("profile_in") == "other_workspace" else None,
        )
    if input_data.get("confirmation") is not None:
        world.add_confirmation(
            case,
            input_data["confirmation"],
            workspace=elsewhere if input_data.get("confirmation_in") == "other_workspace" else None,
        )
    if input_data.get("terms"):
        terms = dict(input_data["terms"])
        if "expected_delivery_date" in terms:
            terms["expected_delivery_date"] = date.fromisoformat(terms["expected_delivery_date"])
        world.set_terms(case, **terms)
    drafted = (await world.lane().run()).drafted
    drafts = [d for d in world.drafts.rows if d.doc_type is DocumentType.PURCHASE_ORDER]
    out: dict[str, Any] = {"drafts": drafted, "fields": {}, "findings": [], "texts": []}
    out["texts"] = [json.dumps(world.notifier.sent, default=str, ensure_ascii=False)]
    if not drafts:
        out["case_state"] = world.store.cases[case.id.value].state.value
        return out
    draft = drafts[0]
    out["fields"] = {k: v.get("value") for k, v in draft.fields.items() if isinstance(v, dict)}
    draft_id = draft.id
    if input_data.get("edit"):
        draft_id = _edit(world, draft, input_data["edit"])
    page = await world.page().handle(world.context(), case.id)
    out["findings"] = [f.code for f in page.findings]
    out["texts"].append(" ".join(f.message for f in page.findings))
    decide = input_data.get("decide")
    if decide is not None:
        current = await world.drafts.get(world.context(), draft_id)
        assert current is not None
        try:
            await world.approver_handler().handle(
                world.context(frozenset(decide.get("scopes", _ALL_SCOPES))),
                case.id,
                draft_id=current.id,
                content_sha256=current.content_sha256,
                po_reference=decide.get("po_reference", "PO-2026-0101"),
            )
            out["decision"] = "po_created"
        except (DomainError, ConflictError, PermissionDeniedError):
            out["decision"] = "refused"
        out["texts"].append(json.dumps(world.notifier.sent, default=str, ensure_ascii=False))
    out["case_state"] = world.store.cases[case.id.value].state.value
    return out


def _lines(fields: dict[str, Any]) -> list[list[Any]]:
    rows = fields.get("lines") or []
    return [
        [r.get("sku_code"), r.get("quantity"), r.get("unit_price"), r.get("line_total")]
        for r in rows
    ]


def grade_purchase_order(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    out = asyncio.run(_run(input_data))
    for key in ("drafts", "decision", "case_state"):
        if key in expected and out.get(key) != expected[key]:
            return GradeResult.fail(key, expected=expected[key], actual=out.get(key))
    for name, value in expected.get("draft_fields", {}).items():
        actual = _lines(out["fields"]) if name == "lines" else out["fields"].get(name)
        if actual != value:
            return GradeResult.fail(f"draft field {name}", expected=value, actual=actual)
    missing = [c for c in expected.get("findings", []) if c not in out["findings"]]
    if missing:
        return GradeResult.fail("findings not named", missing=missing, named=out["findings"])
    texts = " ".join(out["texts"])
    for text in expected.get("must_not_contain", []):
        if text in texts:
            return GradeResult.fail("a notice or finding holds", text=text)
    return GradeResult.ok(drafts=out["drafts"])


__all__ = ["grade_purchase_order"]
