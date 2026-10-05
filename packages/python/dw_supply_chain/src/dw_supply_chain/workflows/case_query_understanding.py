"""Case query understanding — one model call, schema-validated.

A plain function, not a graph, for the same reason as
`supplier_update_understanding`: one structured extraction with nothing to
orchestrate. Takes `ModelGateway` by injection, so it holds no provider SDK
and no SQL.

The model sees the question and nothing else — no supplier list, no case,
nothing tenant-specific. What it returns is a claim; `domain.case_query`
grounds it in the question and resolves it against real data.
"""

from __future__ import annotations

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest
from dw_supply_chain.domain.case_query import CaseQueryIntent

PROMPT_ID = "supply_chain.case_query_understanding"
PROMPT_VERSION = "1.0.0"


async def understand_case_query(
    gateway: ModelGateway, run_context: RunContext, question: str
) -> CaseQueryIntent:
    """The model's reading of `question`, schema-validated by the gateway —
    not yet grounded or resolved; that is the caller's job: the model
    interprets, code decides."""
    request = ModelRequest(
        task="structured_extraction",
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        variables={"question": question},
        route_kind="structured_extraction",
    )
    return await gateway.generate_structured(request, CaseQueryIntent, run_context=run_context)
