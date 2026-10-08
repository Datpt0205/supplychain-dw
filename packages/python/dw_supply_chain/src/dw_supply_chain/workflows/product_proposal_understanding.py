"""Product proposal understanding — one model call, schema-validated.

A plain function, not a graph, for the same reason as
`case_query_understanding`: one structured extraction with nothing to
orchestrate. Takes `ModelGateway` by injection, so it holds no provider SDK and
no SQL.

The model sees the one chat message and nothing else — no draft, no code list,
nothing tenant-specific. What it returns is a claim; `domain.product_proposal`
grounds it in the message and the caller decides against real data.
"""

from __future__ import annotations

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest
from dw_supply_chain.domain.product_proposal import ProductProposalIntent

PROMPT_ID = "supply_chain.product_proposal_understanding"
PROMPT_VERSION = "1.2.0"


def message_as_data(message: str) -> str:
    """The message for the prompt's `<input>` block. A person typed it, so it
    is data the model must not obey: `<` and `>` are written as `\\u003c` and
    `\\u003e` (the prompt says so), and no message can spell the prompt's own
    `</input>` and close the data block early. A quote the model copies from
    the escaped form is not in the original message and is dropped by
    `ground` — the escape can cost a value, never add one."""
    return message.replace("<", "\\u003c").replace(">", "\\u003e")


async def understand_product_proposal(
    gateway: ModelGateway, run_context: RunContext, message: str
) -> ProductProposalIntent:
    """The model's reading of `message`, schema-validated by the gateway — not
    yet grounded; that is the caller's job: the model interprets, code decides."""
    request = ModelRequest(
        task="structured_extraction",
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        variables={"message": message_as_data(message)},
        route_kind="structured_extraction",
    )
    return await gateway.generate_structured(
        request, ProductProposalIntent, run_context=run_context
    )
