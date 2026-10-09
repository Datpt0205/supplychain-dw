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

Elmich's steps 3-5 as ticket ai-automation/09 prepares them (`sample_round`)
add R&D's `measurements` (possibly entered in another workspace) and the
model's words for the record and the request: scripted (`writing`), or, in a
model gate run (`GraderContext.model`), written live by the profile under test.
Their expectations: `model_calls`; `tables` (by document type and field, the
rows code must have drafted, a requirement null where none may be kept);
`notes_must_not_contain`; `scripted` (expectations that hold only for the
scripted words). Whatever the model wrote, every number in a kept note or
requirement is checked again against the evidence it was shown.

Otherwise no model call: the model's work there is the extraction lane's
reading, given by the case. What this grades is what code guarantees whatever
a reading or a model says.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

from dw_agent_runtime.ports import ModelOutputInvalidError
from dw_evals.graders import GraderContext, GradeResult
from dw_kernel.errors import DomainError
from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.extraction import ExtractionStatus, numbers_in
from dw_supply_chain.domain.product_development_case import ProductDevState
from dw_supply_chain.domain.sample_evaluation import EvaluationWriting
from dw_supply_chain.testing.extraction import RecordingGateway, ScriptedGateway
from dw_supply_chain.testing.step_preparation import (
    NOW,
    SAMPLE_ROUND,
    SAMPLE_TESTING,
    SUPPLIER_CONFIRMATION,
    StepWorld,
)

_STEPS = {
    "sample_testing": SAMPLE_TESTING,
    "supplier_confirmation": SUPPLIER_CONFIRMATION,
    "sample_round": SAMPLE_ROUND,
}


def _gateway(ctx: GraderContext, input_data: dict[str, Any]) -> Any:
    if input_data["step"] != "sample_round":
        return None
    if ctx.model is not None:
        return RecordingGateway(ctx.prompt_registry, ctx.model)
    scripted = input_data.get("writing")
    answer: Any = (
        ModelOutputInvalidError("the answer did not fit the schema")
        if scripted == "invalid"
        else EvaluationWriting.model_validate(scripted or {})
    )
    return ScriptedGateway(ctx.prompt_registry, answer=answer)


async def _run(ctx: GraderContext, input_data: dict[str, Any]) -> dict[str, Any]:
    step = _STEPS[input_data["step"]]
    world = StepWorld(gateway=_gateway(ctx, input_data), model_profile=ctx.model_profile)
    elsewhere = uuid.uuid4()
    case_tenant = elsewhere if input_data.get("case_in") == "other_tenant" else None
    case_workspace = uuid.uuid4() if input_data.get("case_in") == "other_workspace" else None
    case, entered = world.add_case(
        ProductDevState(step.state.value),
        tenant=case_tenant,
        workspace=case_workspace,
        product_name=input_data.get("product_name", "Nồi inox 3 đáy 24cm"),
        category=input_data.get("category", "noi"),
    )
    for spec in input_data.get("measurements", []):
        world.measure(case, spec["criterion"], spec["value"])
        if input_data.get("measurements_in") == "other_workspace":
            tenant, _, case_id, m = world.measurements.rows[-1]
            world.measurements.rows[-1] = (tenant, uuid.uuid4(), case_id, m)
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
        "by_type": {d.doc_type.value: (dict(d.fields), d.gaps) for d in world.drafts.rows},
        "model_calls": 0 if world.gateway is None else len(world.gateway.sent),
        "evidence": ""
        if world.gateway is None or not world.gateway.sent
        else world.gateway.sent[-1].user,
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


def _numbers(text: str) -> set[Any]:
    return {abs(n) for n in numbers_in(text)}


def _ai_words(out: dict[str, Any]) -> list[str]:
    """Every note and requirement the drafts keep as AI-written."""
    words: list[str] = []
    for fields, _ in out["by_type"].values():
        notes = fields.get("notes")
        if isinstance(notes, dict) and isinstance(notes.get("value"), str):
            words.append(notes["value"])
        items = fields.get("items")
        if isinstance(items, dict) and isinstance(items.get("value"), list):
            words.extend(
                str(row["requirement"]) for row in items["value"] if row.get("requirement")
            )
    return words


def _grade_round(
    ctx: GraderContext, out: dict[str, Any], expected: dict[str, Any]
) -> GradeResult | None:
    if "model_calls" in expected and out["model_calls"] != expected["model_calls"]:
        return GradeResult.fail(
            "model calls", expected=expected["model_calls"], actual=out["model_calls"]
        )
    for doc_type, fields in expected.get("tables", {}).items():
        stored = out["by_type"].get(doc_type)
        if stored is None:
            return GradeResult.fail("draft missing", doc_type=doc_type)
        for name, rows in fields.items():
            entry = stored[0].get(name)
            actual = entry.get("value") if isinstance(entry, dict) else None
            if not isinstance(actual, list) or len(actual) != len(rows):
                return GradeResult.fail(f"{doc_type}.{name} rows", expected=rows, actual=actual)
            for want, got in zip(rows, actual, strict=True):
                for column, value in want.items():
                    if value == "*":
                        if not got.get(column):
                            return GradeResult.fail(f"{doc_type}.{name}.{column} empty")
                    elif got.get(column) != value:
                        return GradeResult.fail(
                            f"{doc_type}.{name}.{column}", expected=value, actual=got.get(column)
                        )
    words = _ai_words(out)
    for text in expected.get("notes_must_not_contain", []):
        if any(text in w for w in words):
            return GradeResult.fail("AI words hold", text=text)
    shown = _numbers(out["evidence"])
    for w in words:
        if not _numbers(w) <= shown:
            return GradeResult.fail("kept a number the model was not shown", text=w)
    if ctx.model is None:
        for text in expected.get("scripted", {}).get("notes_must_contain", []):
            if not any(text in w for w in words):
                return GradeResult.fail("AI words lack", text=text)
    return None


def grade_step_preparation(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    out = asyncio.run(_run(ctx, input_data))
    if input_data["step"] == "sample_round":
        failed = _grade_round(ctx, out, expected)
        if failed is not None:
            return failed
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
