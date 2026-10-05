"""Delay Impact Analysis — one model call, schema-validated.

Same shape as `workflows.supplier_update_understanding`: no compiled
LangGraph graph for one call, `ModelGateway` taken by injection. The model
is asked for assumptions and mitigation options only — see `domain.
delay_impact`'s own docstring for why WHICH milestones are impacted and
their first-pass day estimate are not part of what this call asks for.
"""

from __future__ import annotations

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest
from dw_supply_chain.domain.delay_impact import DelayImpactExtraction, ImpactedMilestoneEstimate
from dw_supply_chain.domain.po_case import POCase

PROMPT_ID = "supply_chain.delay_impact_analysis"
PROMPT_VERSION = "1.0.0"


async def analyze_delay_impact(
    gateway: ModelGateway,
    run_context: RunContext,
    *,
    case: POCase,
    raw_text: str,
    delay_days: int,
    impacted: list[ImpactedMilestoneEstimate],
) -> DelayImpactExtraction:
    """Ask the model for assumptions and mitigation options.

    `impacted` is trusted, code-computed context (not attacker-controlled
    like `raw_text`), so it is rendered outside the prompt's `<input>` block
    — same distinction `supplier_update_understanding.py` draws between the
    raw message and everything the system already knows to be true.
    """
    request = ModelRequest(
        task="structured_extraction",
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        variables={
            "po_reference": case.po_reference,
            "supplier_name": case.supplier_name,
            "delay_days": str(delay_days),
            "impacted_milestones": ", ".join(e.milestone.value for e in impacted),
            "raw_text": raw_text,
        },
        route_kind="structured_extraction",
    )
    return await gateway.generate_structured(
        request, DelayImpactExtraction, run_context=run_context
    )
