"""Meters the LangChain path into the same usage ledger as the gateway.

``RoutingModelGateway`` records every ``generate_structured`` call, but an
agent harness talks to its model through ``ChatModelFactory`` — a bare
LangChain chat model with no recorder — so until 2026-08-26 every agent-loop
token was invisible to every usage recorder. This adapter
closes that seam: wrap the harness invocation in :meth:`LangchainUsageMeter.track`,
pass the yielded callbacks into the LangChain ``config``, and on exit each
model's ``usage_metadata`` becomes one ledger row priced from the profile's
own route price — the same table the gateway prices from, so one bill speaks
one currency.

Trackers nest, and the ledger counts each token once anyway. LangChain
propagates a parent's callbacks into every nested runnable — that is the
property the runner relies on to meter a whole graph from one seam — so a
handler attached inside a node observes the same model calls the runner's
handler already saw. Two handlers, one call, two ledger rows: measured
2026-08-28, ``sales_research`` and ``sales_signals`` were billing double,
6.1% of the ledger. The tokens were spent once; only the book was wrong.

So the trackers agree among themselves. Each run keeps a tally of what the
trackers inside it have already recorded, and the outermost one records only
the remainder — the calls no inner tracker claimed. The inner rows keep their
own task names, which is what makes a per-lane cost question answerable, and
the totals add up to what was actually spent.

Lives in ``adapters/`` because it imports ``langchain_core`` (import-linter
boundary); the recorder port and pricing helpers come from ``model/``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import route_cost
from dw_agent_runtime.model.gateway import ModelUsage, UsageRecorderPort
from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_agent_runtime.ports import ModelRequest
from dw_platform.application.access_context import AccessContext

_LOG = logging.getLogger("dw_agent_runtime.langchain_usage")

# What the trackers inside one run have already put on the ledger, per model.
# Set by the outermost tracker and mutated by the inner ones, so the outermost
# can record the remainder instead of the whole run a second time.
#
# A ContextVar rather than an attribute because the meter is a shared frozen
# singleton and this state belongs to one invocation. It propagates into tasks
# the run spawns, which is what makes it work for a graph whose nodes fan out,
# and it cannot leak between two runs on the same event loop.
_CLAIMED: ContextVar[dict[str, tuple[int, int]] | None] = ContextVar(
    "dw_langchain_usage_claimed", default=None
)


@dataclass(frozen=True)
class LangchainUsageMeter:
    """Prices and records what a LangChain harness spent in one invocation."""

    profiles: ModelProfileRegistry
    recorder: UsageRecorderPort

    @asynccontextmanager
    async def track(
        self,
        run_context: RunContext,
        *,
        profile_id: str,
        task: str,
        prompt_id: str,
        prompt_version: str,
    ) -> AsyncIterator[list[UsageMetadataCallbackHandler]]:
        """Yield the callbacks to pass as ``config={"callbacks": ...}``.

        The prompt is required: the ledger's prompt columns say which versioned
        artifact the money was spent running, and a default would be a version
        that names none.

        Recording happens in ``finally``: a run that times out or raises has
        still spent its tokens, and the truncated run is exactly the one whose
        bill gets asked about.

        Nested trackers settle between themselves. An inner one records what it
        saw under its own task name and claims those tokens; the outermost
        records only what nobody claimed. The inner block always exits first —
        it is inside the outer ``async with`` — so by the time the outermost
        records, the tally is complete.
        """
        handler = UsageMetadataCallbackHandler()
        claimed = _CLAIMED.get()
        outermost = claimed is None
        # `is None`, never a truth test: the outermost tracker starts this
        # empty, and an empty dict is falsy — `get() or {}` would hand every
        # inner tracker a fresh throwaway to write its claim into, which is
        # this fix failing silently in exactly the way it was fixing.
        tally: dict[str, tuple[int, int]] = {} if outermost else claimed  # type: ignore[assignment]
        token = _CLAIMED.set(tally) if outermost else None
        try:
            yield [handler]
        finally:
            if token is not None:
                _CLAIMED.reset(token)
            await self._record(
                run_context,
                handler,
                profile_id,
                task,
                prompt_id,
                prompt_version,
                # The outermost subtracts what the run already recorded; an
                # inner one records what it saw and adds it to the tally.
                already=tally if outermost else None,
                tally=None if outermost else tally,
            )

    async def _record(
        self,
        run_context: RunContext,
        handler: UsageMetadataCallbackHandler,
        profile_id: str,
        task: str,
        prompt_id: str,
        prompt_version: str,
        already: dict[str, tuple[int, int]] | None = None,
        tally: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        try:
            route = self.profiles.resolve(profile_id).chat
            request = ModelRequest(
                task=task,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                model_profile=profile_id,
            )
            for model_name, metadata in handler.usage_metadata.items():
                input_tokens = int(metadata.get("input_tokens", 0))
                output_tokens = int(metadata.get("output_tokens", 0))
                if tally is not None:
                    seen_in, seen_out = tally.get(model_name, (0, 0))
                    tally[model_name] = (seen_in + input_tokens, seen_out + output_tokens)
                if already is not None:
                    claimed_in, claimed_out = already.get(model_name, (0, 0))
                    input_tokens = max(0, input_tokens - claimed_in)
                    output_tokens = max(0, output_tokens - claimed_out)
                    if not input_tokens and not output_tokens:
                        # Every call under this run was claimed by an inner
                        # tracker. A zero row would be a second entry for the
                        # same work, which is the whole failure being fixed.
                        continue
                usage = ModelUsage(
                    provider=str(route.provider) if route else "openai_compatible",
                    model=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    # Priced from the profile's chat route, like the gateway
                    # prices its routes: providers do not return cost. A model
                    # answering under another name than the route's still
                    # bills at the route's price — the route is what was
                    # configured and paid for.
                    cost_usd=route_cost(route, input_tokens, output_tokens) if route else 0.0,
                )
                await self.recorder.record(run_context, request, usage)
        except Exception:
            _LOG.exception("usage recording failed for run %s", run_context.run_id)


def usage_run_context(
    context: AccessContext, *, worker_id: str, channel: str = "api"
) -> RunContext:
    """A ledger-grade RunContext for a call that has no worker run of its own.

    The sync services (stakeholder extract/arrange/digest, account enrich)
    answer inside one HTTP request — no ``worker_runs`` row exists, so the
    minted ``run_id`` is the invocation's identity: one click, one id, and
    ``COUNT(DISTINCT run_id)`` in the ledger is the usage counter F6 reads.
    """
    run_id = uuid.uuid4()
    return RunContext(
        run_id=run_id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        actor_id=context.principal_id,
        worker_id=worker_id,
        worker_version="0.0.0",
        channel=channel,
        plan_id=context.plan_id,
        roles=context.roles,
        scopes=context.scopes,
        trace_id=str(run_id),
    )


async def metered_invoke(
    model: Any,
    messages: list[dict[str, str]],
    *,
    meter: LangchainUsageMeter | None,
    context: AccessContext,
    worker_id: str,
    profile_id: str,
    task: str,
    prompt_id: str,
    prompt_version: str,
    timeout_seconds: float,
) -> Any:
    """One structured LangChain call, timed out and — when a meter is wired —
    billed under its own fresh run id. ``None`` meter keeps the exact old
    behaviour, so hosts without a database still answer."""
    if meter is None:
        return await asyncio.wait_for(model.ainvoke(messages), timeout=timeout_seconds)
    run_context = usage_run_context(context, worker_id=worker_id)
    async with meter.track(
        run_context,
        profile_id=profile_id,
        task=task,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
    ) as callbacks:
        return await asyncio.wait_for(
            model.ainvoke(messages, config={"callbacks": callbacks}),
            timeout=timeout_seconds,
        )
