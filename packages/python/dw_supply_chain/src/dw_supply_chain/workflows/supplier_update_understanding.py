"""Supplier Update Understanding — one model call, schema-validated.

No LangGraph graph yet: this is a single structured-extraction call with no
orchestration of its own, and building a compiled graph around one call would
be exactly the empty abstraction CLAUDE.md's Work style warns against. A real
graph is worth it once a second node (Delay Impact Analysis) actually
chains off this one's output — not before.

Takes `ModelGateway` by injection (a port from `dw_agent_runtime.ports`, not
a concrete adapter) so this node contains no provider SDK, matching every
other workflow node in this platform.
"""

from __future__ import annotations

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest
from dw_supply_chain.domain.supplier_update import SupplierUpdateExtraction

PROMPT_ID = "supply_chain.supplier_update_understanding"
PROMPT_VERSION = "1.0.0"


async def understand_supplier_update(
    gateway: ModelGateway, run_context: RunContext, raw_text: str
) -> SupplierUpdateExtraction:
    """Extract a structured event from one raw supplier message.

    Returns the model's claim as-is — schema-validated by the gateway, not
    yet checked against `raw_text` or gated by a confidence threshold. Both
    are the caller's job (`application.handlers.SubmitSupplierUpdate`), per
    the model-interprets, code-decides split: the model turns text into a
    typed, validated intent; code resolves it against real data and
    decides.
    """
    request = ModelRequest(
        task="structured_extraction",
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        variables={"raw_text": raw_text},
        route_kind="structured_extraction",
    )
    return await gateway.generate_structured(
        request, SupplierUpdateExtraction, run_context=run_context
    )
