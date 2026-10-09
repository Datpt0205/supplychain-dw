"""The eval grader of step 12's papers (ticket ai-automation/16),
`supply_chain.packaging_proof`.

It runs the REAL `PreparePackagingPapers` and `GetPackagingProof` over the
in-memory world of `testing.packaging_papers` (stores that keep RLS), with the
shipped templates. No model is called here: the model's part is reading the
proof, which the case gives (the `extract.packaging_design` cases grade that
on their own). What this grades is what code guarantees whatever the reading
says.

A case gives the proof's reading (`proof`, possibly read in another
workspace: `proof_in`; null: no proof), the product's BM04 (`profile`: true
for the world's, false for none; possibly saved in another workspace:
`profile_in`), whether the colour is approved (`colour_approved`), a colour
sample (`colour_sample`), and where the case lives (`case_in`). Expected:
`findings` (codes, or `code:subject`, that must be named), `findings_absent`,
`drafted` (count by document type), `items` (subjects of the design request),
`draft_fields` (by document type, value by name; null: must be empty),
`proof_status` and `must_not_contain`.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from dw_evals.graders import GraderContext, GradeResult
from dw_kernel.errors import NotFoundError
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.packaging_design import ReviewStatus
from dw_supply_chain.testing.packaging_papers import PackagingWorld, drafted_values


async def _run(input_data: dict[str, Any]) -> dict[str, Any]:
    world = PackagingWorld()
    elsewhere = uuid.uuid4()
    tenant = uuid.uuid4() if input_data.get("case_in") == "other_tenant" else None
    case = world.add_case(tenant=tenant)
    if input_data.get("profile", True):
        world.add_profile(
            case, workspace=elsewhere if input_data.get("profile_in") == "other_workspace" else None
        )
    if input_data.get("colour_approved"):
        world.set_design(case, colour_status=ReviewStatus.APPROVED)
    if input_data.get("colour_sample"):
        world.add_document(case, DocumentType.COLOUR_SAMPLE, status=None)
    if input_data.get("proof") is not None:
        world.add_document(
            case,
            DocumentType.PACKAGING_DESIGN,
            input_data["proof"],
            workspace=elsewhere if input_data.get("proof_in") == "other_workspace" else None,
        )
    await world.lane().run()
    out: dict[str, Any] = {"findings": [], "proof_status": None, "texts": []}
    try:
        page = await world.proof_page().handle(world.context(), case.id)
        out["findings"] = [f"{f.code}:{f.subject}" for f in page.findings]
        out["proof_status"] = (
            None if page.proof is None or page.proof.status is None else page.proof.status.value
        )
        out["texts"].append([f.message for f in page.findings])
    except NotFoundError:
        out["proof_status"] = "not_found"
    out["drafted"] = {
        t.value: len(world.drafted(t))
        for t in (
            DocumentType.PACKAGING_CONTENT,
            DocumentType.USER_MANUAL,
            DocumentType.COLOUR_REVISION_REQUEST,
            DocumentType.DESIGN_REVISION_REQUEST,
        )
    }
    out["values"] = {t: drafted_values(world.drafted(DocumentType(t))) for t in out["drafted"]}
    out["texts"].append([f"{n.get('title', '')} {n.get('body', '')}" for n in world.notifier.sent])
    return out


def grade_packaging_proof(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    out = asyncio.run(_run(input_data))
    named = set(out["findings"]) | {f.split(":", 1)[0] for f in out["findings"]}
    missing = [f for f in expected.get("findings", []) if f not in named]
    if missing:
        return GradeResult.fail("findings not named", missing=missing, named=out["findings"])
    present = [f for f in expected.get("findings_absent", []) if f in named]
    if present:
        return GradeResult.fail("findings that must not be named", named=present)
    for doc_type, count in expected.get("drafted", {}).items():
        if out["drafted"].get(doc_type) != count:
            return GradeResult.fail(
                f"drafted {doc_type}", expected=count, actual=out["drafted"].get(doc_type)
            )
    if "items" in expected:
        values = out["values"].get("design_revision_request") or [{}]
        subjects = sorted(i["subject"] for i in values[0].get("items", []))
        if subjects != sorted(expected["items"]):
            return GradeResult.fail(
                "design request items", expected=expected["items"], actual=subjects
            )
    for doc_type, wanted in expected.get("draft_fields", {}).items():
        values = out["values"].get(doc_type) or [{}]
        for name, value in wanted.items():
            if values[0].get(name) != value:
                return GradeResult.fail(
                    f"{doc_type}.{name}", expected=value, actual=values[0].get(name)
                )
    if "proof_status" in expected and out["proof_status"] != expected["proof_status"]:
        return GradeResult.fail(
            "proof status", expected=expected["proof_status"], actual=out["proof_status"]
        )
    texts = json.dumps(out["texts"], default=str, ensure_ascii=False)
    for text in expected.get("must_not_contain", []):
        if text in texts:
            return GradeResult.fail("a notice or finding holds", text=text)
    return GradeResult.ok(findings=len(out["findings"]))


__all__ = ["grade_packaging_proof"]
