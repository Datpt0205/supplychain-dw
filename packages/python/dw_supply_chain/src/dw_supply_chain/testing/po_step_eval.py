"""The eval grader of a PO case's steps prepared by code (tickets
ai-automation/15-18), `supply_chain.po_step`.

It runs the REAL `PreparePOSteps`, `GetPOStepProposal` and `ApprovePOStep`
over the in-memory world of `testing.po_steps` (stores that keep RLS, the
decisions' UNIQUE and the case's version), with the shipped templates and the
real renderer. No model is called here: the model's part is the extraction
lane's reading of each paper, which the case gives (and which the extraction
cases grade on their own). What this grades is what code guarantees whatever
a reading says.

A case names the step (`step`), the PO case (`lines` as `[quantity, price]`,
`deposit_percent`, possibly another tenant's: `case_in`), the supplier's
master account (`master`, possibly saved in another workspace: `master_in`),
the documents on the case with their readings (`documents`: `doc_type`,
`reading`, `accounts` the paper names, `status`, possibly read in another
workspace: `in`), payments already recorded (`payments`), a person's edit of
the draft (`edit`) and a decision (`decide`: `scopes`, `results`). Expected:
`drafts`, `draft_fields` (null: must be empty), `findings` (codes that must
be named), `findings_absent`, `proposed`, `decision` (`applied`/`refused`),
`case_state`, `payment` (the payment the decision recorded, null: none) and
`must_not_contain` (text no notice and no finding may hold: an amount, an
account).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from dw_evals.graders import GraderContext, GradeResult
from dw_kernel.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError
from dw_supply_chain.application.document_drafts import NewDocumentDraft
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.commercial import PaymentKind
from dw_supply_chain.domain.document_draft import DocumentDraft, content_sha256
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.po_step import PO_STEPS, POStepKind
from dw_supply_chain.testing.po_steps import PO_STEP_SCOPES, POStepWorld


def _edit(world: POStepWorld, draft: DocumentDraft, edit: dict[str, Any]) -> uuid.UUID:
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


def _words(world: POStepWorld) -> list[str]:
    """What a person reads in a notice: its title and body (ids are random and
    may spell any number)."""
    return [f"{n.get('title', '')} {n.get('body', '')}" for n in world.notifier.sent]


async def _run(input_data: dict[str, Any]) -> dict[str, Any]:
    kind = POStepKind(input_data["step"])
    spec = PO_STEPS[kind]
    world = POStepWorld()
    elsewhere = uuid.uuid4()
    if input_data.get("master") is not None:
        world.add_master_account(
            input_data["master"],
            workspace=elsewhere if input_data.get("master_in") == "other_workspace" else None,
        )
    case = world.add_case(
        spec.state,
        lines=[tuple(line) for line in input_data.get("lines", [[500, "2.50"], [1200, "2.50"]])],
        deposit_percent=input_data.get("deposit_percent", "30"),
        tenant=uuid.uuid4() if input_data.get("case_in") == "other_tenant" else None,
    )
    for spec_doc in input_data.get("documents", []):
        status = spec_doc.get("status", "extracted")
        world.add_document(
            case,
            DocumentType(spec_doc["doc_type"]),
            spec_doc.get("reading", {}),
            accounts=spec_doc.get("accounts", []),
            status=None if status is None else ExtractionStatus(status),
            workspace=elsewhere if spec_doc.get("in") == "other_workspace" else None,
        )
    for payment in input_data.get("payments", []):
        world.add_payment(case, PaymentKind(payment["kind"]), payment["amount"])
    drafted = (await world.lane().run()).drafted
    out: dict[str, Any] = {"drafts": drafted, "fields": {}, "texts": [_words(world)]}
    draft: DocumentDraft | None = None
    if spec.draft is not None:
        mine = [
            d
            for d in await world.drafts.latest_for_case(world.context(), CaseKind.PO, case.id.value)
            if d.doc_type is spec.draft
        ]
        draft = mine[0] if mine else None
    if draft is not None:
        out["fields"] = {k: v.get("value") for k, v in draft.fields.items() if isinstance(v, dict)}
        if input_data.get("edit"):
            edited = await world.drafts.get(
                world.context(), _edit(world, draft, input_data["edit"])
            )
            draft = edited or draft
    try:
        page = await world.page().handle(world.context(), case.id)
        out["findings"] = [f.code for f in page.findings]
        out["proposed"] = page.proposed
        out["texts"].append([f.message for f in page.findings])
    except NotFoundError:
        out["findings"], out["proposed"] = [], False
    decide = input_data.get("decide")
    if decide is not None:
        try:
            await world.approver().handle(
                world.context(frozenset(decide.get("scopes", PO_STEP_SCOPES))),
                case.id,
                kind=kind,
                draft_id=None if draft is None else draft.id,
                content_sha256=None if draft is None else draft.content_sha256,
                results=decide.get("results", {}),
            )
            out["decision"] = "applied"
        except (DomainError, ConflictError, PermissionDeniedError, NotFoundError):
            out["decision"] = "refused"
        out["texts"].append(_words(world))
    out["case_state"] = world.store.cases[case.id.value].state.value
    recorded = [
        p for p in world.store.payments.get(case.id.value, []) if p.recorded_by == world.person
    ]
    out["payment"] = (
        None
        if not recorded
        else {"kind": recorded[-1].kind.value, "amount": str(recorded[-1].amount)}
    )
    return out


def grade_po_step(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    out = asyncio.run(_run(input_data))
    for key in ("drafts", "proposed", "decision", "case_state", "payment"):
        if key in expected and out.get(key) != expected[key]:
            return GradeResult.fail(key, expected=expected[key], actual=out.get(key))
    for name, value in expected.get("draft_fields", {}).items():
        if out["fields"].get(name) != value:
            return GradeResult.fail(
                f"draft field {name}", expected=value, actual=out["fields"].get(name)
            )
    missing = [c for c in expected.get("findings", []) if c not in out["findings"]]
    if missing:
        return GradeResult.fail("findings not named", missing=missing, named=out["findings"])
    present = [c for c in expected.get("findings_absent", []) if c in out["findings"]]
    if present:
        return GradeResult.fail("findings that must not be named", named=present)
    texts = json.dumps(out["texts"], default=str, ensure_ascii=False)
    for text in expected.get("must_not_contain", []):
        if text in texts:
            return GradeResult.fail("a notice or finding holds", text=text)
    return GradeResult.ok(drafts=out["drafts"], findings=len(out["findings"]))


__all__ = ["grade_po_step"]
