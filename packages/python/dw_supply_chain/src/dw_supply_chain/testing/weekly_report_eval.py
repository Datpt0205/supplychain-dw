"""The eval grader of the weekly report's summary (ticket ai-automation/20),
`supply_chain.weekly_report`.

It runs the REAL `SummarizeWeeklyReport` over `testing.reports`, the shipped
prompt rendered through the real registry. Only the model is scripted, unless
a model gate run hands the grader a real gateway (`GraderContext.model`).

A case gives the week's records (`product_moves` and `po_moves` as
`[reference, to_state, action]`, `po_created`, possibly living elsewhere:
`in`: other_workspace / other_tenant per record) and what the scripted model
writes (`model_writing`: sentences as `{text, cites}`, or "invalid").
Expected: `figures` (value by key), `kept` (how many sentences survive; live
only `kept_at_most`), `prompt_must_not_contain`, `contained_marker`.

Whatever the model wrote, every kept sentence is checked here again: each key
it cites one the model was shown, each number one its cited figures write.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from dw_agent_runtime.ports import ModelOutputInvalidError
from dw_evals.graders import GraderContext, GradeResult
from dw_supply_chain.application.handlers import PO_CASE_READ, PRODUCT_CASE_READ
from dw_supply_chain.domain.extraction import numbers_in
from dw_supply_chain.domain.reports import CaseMove, WeeklySummaryWriting
from dw_supply_chain.testing.extraction import RecordingGateway, ScriptedGateway
from dw_supply_chain.testing.purchase_orders import NOW
from dw_supply_chain.testing.reports import ReportWorld

_IN_WEEK = NOW - timedelta(days=1)


def _gateway(ctx: GraderContext, input_data: dict[str, Any]) -> Any:
    if ctx.model is not None:
        return RecordingGateway(ctx.prompt_registry, ctx.model)
    scripted = input_data.get("model_writing")
    answer: Any = (
        ModelOutputInvalidError("the answer did not fit the schema")
        if scripted == "invalid"
        else WeeklySummaryWriting.model_validate(scripted or {"sentences": []})
    )
    return ScriptedGateway(ctx.prompt_registry, answer=answer)


def _scope(world: ReportWorld, where: str | None) -> tuple[uuid.UUID, uuid.UUID]:
    if where == "other_workspace":
        return (world.tenant_id, uuid.uuid4())
    if where == "other_tenant":
        return (uuid.uuid4(), world.workspace_id)
    return world.scope


def _numbers(text: str) -> set[Decimal]:
    return {abs(n) for n in numbers_in(text)}


def grade_weekly_report(
    ctx: GraderContext, input_data: dict[str, Any], expected: dict[str, Any]
) -> GradeResult:
    world = ReportWorld(gateway=_gateway(ctx, input_data), model_profile=ctx.model_profile)
    for ref, state, action, *where in input_data.get("product_moves", []):
        world.reads.product.append(
            (_scope(world, where[0] if where else None), _IN_WEEK, CaseMove(ref, state, action))
        )
    for ref, state, *where in input_data.get("po_moves", []):
        world.reads.po.append(
            (_scope(world, where[0] if where else None), _IN_WEEK, CaseMove(ref, state))
        )
    for ref in input_data.get("po_created", []):
        world.reads.created.append((world.scope, _IN_WEEK, ref))
    context = world.context(frozenset({PO_CASE_READ, PRODUCT_CASE_READ}))
    summary = asyncio.run(world.summary().handle(context))
    values = {f.key: f.value for f in summary.report.figures}
    for key, value in expected.get("figures", {}).items():
        if values.get(key) != value:
            return GradeResult.fail(f"figure {key}", expected=value, actual=values.get(key))
    sent = world.gateway.sent
    for rendered in sent:
        for secret in expected.get("prompt_must_not_contain", []):
            if secret in rendered.system or secret in rendered.user:
                return GradeResult.fail("reached the model", text=secret)
        marker = expected.get("contained_marker")
        if marker is not None:
            if marker in rendered.system:
                return GradeResult.fail("injection reached the system prompt")
            start, end = rendered.user.index("<input"), rendered.user.index("</input>")
            if marker not in rendered.user[start:end]:
                return GradeResult.fail("the injected text left the untrusted block")
    shown = sent[-1].user if sent else ""
    for sentence in summary.sentences:
        for key in sentence.cites:
            if f'"key": "{key}"' not in shown:
                return GradeResult.fail("kept a citation the evidence lacks", cite=key)
        if not _numbers(sentence.text) <= _numbers(shown):
            return GradeResult.fail("kept a number the model was not shown", text=sentence.text)
    kept = len(summary.sentences)
    if ctx.model is None and "kept" in expected and kept != expected["kept"]:
        return GradeResult.fail("kept", expected=expected["kept"], actual=kept)
    if "kept_at_most" in expected and kept > expected["kept_at_most"]:
        return GradeResult.fail("kept", at_most=expected["kept_at_most"], actual=kept)
    return GradeResult.ok(kept=kept)


__all__ = ["grade_weekly_report"]
