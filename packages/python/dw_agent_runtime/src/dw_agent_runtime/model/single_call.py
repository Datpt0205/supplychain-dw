"""A model gateway for a run that is exactly one model call.

The per-run spend ledger (`RunBudgetLedger`) bounds a run that makes many
calls, and the runner frees a run's entry when the run ends. A handler that
calls `ModelGateway.generate_structured` directly — one structured
extraction, no runner — mints a fresh run id per request, so nothing ever
freed its entry: one per call, for the life of the process (measured: 50
calls, 50 entries). That is `failure-modes.md` #6, and a chatty caller such
as a command bar multiplies it.

This wraps the process's gateway for exactly those callers and frees the
entry once the call is over, whatever the outcome. The composition root
decides who gets it; a caller never builds its own ledger.

It is also the one-call door's plan check. A runner checks the tenant's daily
allowance where a run begins; a call made with no run around it never passes
there, so it is checked here, before the call, by the same `DailyAllowance`
(failure-modes #5). Required, not defaulted: a door with no check is the hole
this closes. Built by `ModelStack.one_call`, which both composition roots use.
"""

from __future__ import annotations

from dataclasses import dataclass

from dw_agent_runtime.allowance import DailyAllowance
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.ports import ModelGateway, ModelRequest, OutputT


@dataclass(frozen=True)
class SingleCallModelGateway:
    """Implements `ModelGateway` for a caller whose run is this one call."""

    inner: ModelGateway
    ledger: RunBudgetLedger
    allowance: DailyAllowance

    async def generate_structured(
        self,
        request: ModelRequest,
        output_type: type[OutputT],
        *,
        run_context: RunContext,
    ) -> OutputT:
        # Before the call, and outside the `finally`: a refused call spent
        # nothing and left no entry to free.
        await self.allowance.require(run_context)
        try:
            return await self.inner.generate_structured(
                request, output_type, run_context=run_context
            )
        finally:
            # The run is over when this call is: nothing will ever check
            # this entry again, and nothing else would ever free it.
            self.ledger.forget(run_context.run_id)
