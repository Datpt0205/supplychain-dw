"""The eval grader of step preparation (tickets ai-automation/05 and 06),
`supply_chain.step_preparation`.

It runs the REAL `PrepareStep` and `ApplyStepProposal` over the in-memory world
of `testing.step_preparation` (stores that keep RLS, the decisions' UNIQUE and
the case's version), with the shipped templates and the real renderer. A case
names the step (`sample_testing` or `supplier_confirmation`, Elmich's two), the
source documents and their readings (possibly read in another workspace, or
unreadable), where the case lives, and optionally a decision; and what must come
out:

- `outcome` (`proposed` / `not_prepared`) and `reason`;
- `draft_fields`: value by name in the drafted record (null = must be EMPTY,
  e.g. a result a person types, whatever a source says);
- `suggestions`: AI's reading beside each result field (`{}` = none at all);
- `required_input`, `findings` (codes that must be named), `drafts` (how many
  were written);
- `decision` (`refused`, `rejected`, or the action applied) and `case_state`.

No model call: the model's work here is the extraction lane's reading, given
by the case. What this grades is what code guarantees whatever a reading says.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from dw_evals.graders import GraderContext, GradeResult
from dw_kernel.errors import DomainError
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import ExtractionStatus
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.testing.step_preparation import (
    NOW,
    SAMPLE_TESTING,
    SUPPLIER_CONFIRMATION,
    StepWorld,
)

_STEPS = {"sample_testing": SAMPLE_TESTING, "supplier_confirmation": SUPPLIER_CONFIRMATION}


async def _run(input_data: dict[str, Any]) -> dict[str, Any]:
    step = _STEPS[input_data["step"]]
    world = StepWorld()
    elsewhere = uuid.uuid4()
    case_tenant = elsewhere if input_data.get("case_in") == "other_tenant" else None
    case, entered = world.add_case(ProductDevState(step.state.value), tenant=case_tenant)
    for spec in input_data.get("documents", []):
        document = world.add_document(
            case,
            DocumentType(spec["doc_type"]),
            text=spec["text"],
            version=spec.get("version", 1),
            uploaded_at=NOW - timedelta(hours=spec.get("uploaded_hours_ago", 2)),
        )
        reading = world.add_reading(
            document,
            spec.get("reading", {}),
            status=ExtractionStatus(spec.get("status", "extracted")),
        )
        if spec.get("reading_in") == "other_workspace":
            world.readings.rows[-1] = (document.tenant_id, uuid.uuid4(), reading)
    # The lane runs in the world's own tenant and workspace.
    lane = world.context(world.lane_context(case).principal_id)
    prepared = await world.preparer().prepare(lane, world.request(case, entered, step))
    out: dict[str, Any] = {
        "outcome": prepared.outcome.value,
        "reason": prepared.reason,
        "payload": prepared.payload,
        "drafts": len(world.drafts.rows),
        "draft_fields": dict(world.drafts.rows[0].fields) if world.drafts.rows else {},
    }
    decide = input_data.get("decide")
    if decide is not None and prepared.payload:
        try:
            out["decision"] = await world.applier().apply(
                world.context(),
                prepared.payload,
                approved=decide["approve"],
                comment=decide.get("comment", "ok"),
                typed_input=decide.get("typed_input", {}),
                run_id=None,
            )
        except DomainError:
            out["decision"] = "refused"
    stored = world.cases.cases.get(case.id.value)
    out["case_state"] = None if stored is None else stored.state.value
    return out


def grade_step_preparation(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    out = asyncio.run(_run(input_data))
    for key in ("outcome", "reason", "drafts", "decision", "case_state"):
        if key in expected and out.get(key) != expected[key]:
            return GradeResult.fail(key, expected=expected[key], actual=out.get(key))
    for name, value in expected.get("draft_fields", {}).items():
        entry = out["draft_fields"].get(name)
        actual = entry.get("value") if isinstance(entry, dict) else None
        if actual != value:
            return GradeResult.fail(f"draft field {name}", expected=value, actual=actual)
    payload = out["payload"]
    if "suggestions" in expected:
        actual = {k: v.get("value") for k, v in payload.get("suggestions", {}).items()}
        if actual != expected["suggestions"]:
            return GradeResult.fail("suggestions", expected=expected["suggestions"], actual=actual)
    if "required_input" in expected and payload.get("required_input") != expected["required_input"]:
        return GradeResult.fail("required input", actual=payload.get("required_input"))
    codes = {f["code"] for f in payload.get("findings", [])}
    missing = [c for c in expected.get("findings", []) if c not in codes]
    if missing:
        return GradeResult.fail("findings not named", missing=missing, named=sorted(codes))
    return GradeResult.ok(outcome=out["outcome"])
