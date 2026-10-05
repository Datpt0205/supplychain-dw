"""The daily brief's AI summary — one model call, schema-validated.

A plain function, not a graph, for the same reason as the other Supply Chain
model calls: one structured call with nothing to orchestrate. Takes
`ModelGateway` by injection, so it holds no provider SDK and no SQL.

What the model sees is the brief itself and nothing else: the groups a
reader is shown, as data inside the prompt's `<input>` block. Its answer is
a claim; `domain.brief_summary.ground_summary` decides which sentences a
person reads.
"""

from __future__ import annotations

import json

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest
from dw_supply_chain.domain.brief_summary import BriefSummaryDraft
from dw_supply_chain.domain.daily_brief import DailyBrief

PROMPT_ID = "supply_chain.daily_brief_summary"
PROMPT_VERSION = "1.0.0"


def brief_as_data(brief: DailyBrief) -> str:
    """The brief as JSON for the prompt's `<input>` block — the groups and
    the cases a reader is shown, in the brief's own order.

    Supplier names and PO references were typed by people, so they are data
    the model must not obey. `<` and `>` are written as `\\u003c`/`\\u003e`,
    which JSON reads back as the same characters: no value can spell the
    prompt's own `</input>` and close the data block early."""
    groups = [
        {
            "key": group.key,
            "signal": group.signal.value,
            "qualifier": group.qualifier,
            "total": group.total,
            "cases": [
                {
                    "po_reference": entry.case.po_reference,
                    "supplier_name": entry.case.supplier_name,
                    "state": entry.case.state.value,
                    "days": entry.days,
                    "limit_days": entry.limit_days,
                }
                for entry in group.shown_entries
            ],
        }
        for group in brief.groups
    ]
    text = json.dumps(
        {"active_case_count": brief.active_case_count, "groups": groups}, ensure_ascii=False
    )
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


async def summarize_brief(
    gateway: ModelGateway, run_context: RunContext, brief: DailyBrief
) -> BriefSummaryDraft:
    """The model's summary of `brief`, schema-validated by the gateway — not
    yet checked against the brief; that is the caller's job."""
    request = ModelRequest(
        task="reasoning",
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        variables={"brief": brief_as_data(brief)},
        route_kind="reasoning",
    )
    return await gateway.generate_structured(request, BriefSummaryDraft, run_context=run_context)
