"""Context compaction the platform can stand behind.

A run long enough to outgrow the model's context window used to end at the
provider's limit with no way down. Compaction replaces older turns with a
summary. LangChain ships `SummarizationMiddleware` for it, and it was measured
before being used rather than trusted. What follows is what the measurement
found wrong for a multi-tenant platform; this class fixes exactly those things.

1. **It destroys history without a trace.** The update it returns starts with
   `RemoveMessage(REMOVE_ALL_MESSAGES)` — the checkpointed state loses the older
   turns for good, not merely the model's view of them. Measured: 30 messages in,
   6 out, the originals gone. Here every compaction writes an audit event first
   — what was removed, a digest of it, what replaced it — and if that record
   cannot be written, nothing is removed. History is never destroyed without a
   record that it was.

2. **A failing summariser kills the turn.** The summary call raises once its
   retries are spent, and the turn dies — the long run compaction exists to
   save. Here a failed summary leaves the history as it was and lets the turn
   continue: if it still fits, it succeeds; if it does not, it fails at the
   provider's limit exactly as it would have with no compaction at all. Never
   worse than not compacting.

3. **The summary is re-inserted as if the user wrote it** — a `HumanMessage`.
   Tool results carry untrusted text (a web page, a document a customer sent),
   and laundering it through a summariser hands it a user's authority. Here the
   summary is framed as system-made reference data. That lowers the odds and
   guarantees nothing: the guarantee is that every tool still passes scope
   checks and the approval gate in code, whatever the model was persuaded of.

4. **It summarises only the tail, or nothing.** Before summarising, the library
   trims what it removes to its last 4000 tokens, starting on a human message.
   Measured on the pinned version: in a long tool loop the only human message
   is older than that, the trim returns nothing, and the step compacts nothing
   — the context grows until the provider refuses it. On a long thread the trim
   keeps the last few turns, so the previous summary and everything before
   those turns never reach the summariser, and each compaction drops what the
   last one kept while the audit says the whole span was compacted. When even
   that trim leaves nothing, it removes the history anyway and inserts "Previous
   conversation was too long to summarize." Here nothing is trimmed away: what
   is removed is summarised in chunks no larger than the summary route's
   `max_input_tokens`, a tool call never separated from its result, the
   previous summary handed to the first call as the anchor it updates and each
   chunk's summary to the next. A single message larger than that budget
   compacts nothing — never a placeholder.

5. **Its spend is invisible to the run's ceiling.** The summariser is called
   directly, not through the agent's model node, so `RunBudgetMiddleware` never
   sees it. Measured: summariser called, one ledger entry — the agent's. A run
   that kept tripping compaction would spend on summaries with no limit. Here the
   ceiling is checked before every summary call and each call's tokens are added
   to the same ledger after.

Two things measured and left alone because they were already right: a file the
model cannot read never reaches the summariser (history is serialised to text
first), and a pending approval survives compaction (tool call and result are
kept together).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from datetime import UTC
from typing import Any

from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.internal_call_transformer import internal_call_metadata
from langchain.agents.middleware.summarization import ContextSize
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    RemoveMessage,
    ToolMessage,
)
from langchain_core.messages.utils import get_buffer_string
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

from dw_agent_runtime.adapters.chat_model import chat_route
from dw_agent_runtime.context import access_context_from_run
from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.model.budget import RunBudgetLedger, route_cost
from dw_agent_runtime.model.copy import RuntimeCopy
from dw_agent_runtime.model.profiles import ModelProfileRegistry, ModelRoute
from dw_agent_runtime.registry import ConfigError
from dw_kernel.ids import TenantId, UserId, WorkspaceId
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.ports import PlatformUnitOfWorkFactory
from dw_platform.domain.audit import AuditEvent

__all__ = ["COMPACTED_ACTION", "COMPACTION_TASK", "PlatformSummarizationMiddleware"]

logger = logging.getLogger("dw_agent_runtime.context_compaction")

COMPACTED_ACTION = "run.context_compacted"
# What a refused summary is reported under, next to the agent loop's own task.
COMPACTION_TASK = "context_compaction"


class PlatformSummarizationMiddleware(SummarizationMiddleware):
    """Compacts long history — recorded, bounded, failure-tolerant, not user-authored."""

    def __init__(
        self,
        model: BaseChatModel,
        *,
        copy: RuntimeCopy,
        uow_factory: PlatformUnitOfWorkFactory,
        clock: UtcClock,
        ids: IdGenerator,
        budget: RunBudgetLedger,
        profiles: ModelProfileRegistry,
        # The run's profile: its ceiling is the one a summary counts against.
        profile_id: str,
        # The summariser's own profile: its price is what a summary costs. A
        # separate argument because compaction is usually given a cheaper model
        # than the one doing the work.
        summary_profile_id: str,
        trigger: ContextSize,
        keep: ContextSize,
    ) -> None:
        if (
            copy.context_summary_prompt is None
            or copy.context_summary_frame is None
            or copy.context_summary_update_prompt is None
        ):
            # Refuse rather than fall back to the library's own prompt: that one is
            # English, knows nothing of pending approvals, and is exactly what this
            # class exists not to use. Without the update prompt, a summary could
            # not carry the previous one forward.
            raise ConfigError(
                f"runtime copy {copy.version} has no context summary text; "
                "load runtime@1.6.0 or later"
            )
        # At build, on the platform layer: a deployment whose summary route
        # declares no input budget fails here, not on the first long run.
        _input_budget(chat_route(profiles, summary_profile_id), summary_profile_id)
        super().__init__(
            model,
            trigger=trigger,
            keep=keep,
            summary_prompt=copy.context_summary_prompt,
            # Never used: `_summarise` chunks to the route's budget instead of
            # trimming to the library's 4000. None so nothing reading the
            # attribute believes a limit applies that does not.
            trim_tokens_to_summarize=None,
        )
        self._update_prompt = copy.context_summary_update_prompt
        self._copy = copy
        self._uow_factory = uow_factory
        self._clock = clock
        self._ids = ids
        self._budget = budget
        self._profiles = profiles
        self._profile_id = profile_id
        self._summary_profile_id = summary_profile_id

    def _build_new_messages(self, summary: str) -> list[HumanMessage]:  # type: ignore[override]
        return [
            HumanMessage(
                content=self._copy.context_summary(summary),
                # `lc_source` is the library's own marker; the second says, for
                # anything that reads the transcript later, that no person wrote it.
                additional_kwargs={"lc_source": "summarization", "dw_system_generated": True},
            )
        ]

    async def abefore_model(  # type: ignore[override]
        self, state: Any, runtime: Runtime[RunContext]
    ) -> dict[str, Any] | None:
        messages: list[AnyMessage] = state["messages"]
        self._ensure_message_ids(messages)
        if not self._should_summarize(messages, self.token_counter(messages)):
            return None
        cutoff = self._determine_cutoff_index(messages)
        if cutoff <= 0:
            return None
        removed, preserved = self._partition_messages(messages, cutoff)
        run_context = runtime.context
        profile = self._profiles.resolve(self._profile_id, tenant_id=run_context.tenant_id)
        summary_route = chat_route(
            self._profiles, self._summary_profile_id, tenant_id=run_context.tenant_id
        )
        budget = _input_budget(summary_route, self._summary_profile_id)

        anchor, pending = _anchor_and_rest(removed)
        if not pending:
            # Only the previous summary would go: nothing new to fold into it.
            return None
        units = list(_units(pending))
        # The smallest any call's frame can be: the update prompt with an empty
        # anchor. A message that does not fit beside it fits in no call.
        room = budget - self._tokens(self._update_prompt.format(summary="", messages=""))
        if any(self._unit_tokens(unit) > room for unit in units):
            logger.warning(
                "context compaction skipped: a message is larger than the summary "
                "route's max_input_tokens (%d); history left intact",
                budget,
            )
            return None

        calls = 0
        while units:
            used = self._tokens(self._render(anchor=anchor, messages=""))
            chunk: list[AnyMessage] = []
            while units and used + self._unit_tokens(units[0]) <= budget:
                used += self._unit_tokens(units[0])
                chunk.extend(units.pop(0))
            if not chunk:
                # The running summary has grown past what leaves room for the next
                # message. Stopping part-way would drop the rest, so stop whole.
                logger.warning(
                    "context compaction skipped: the running summary leaves no room "
                    "within max_input_tokens (%d); history left intact",
                    budget,
                )
                return None
            # Outside the fail-open block below, on purpose, and before EVERY
            # call: a run over its ceiling must stop. Caught there, the refusal
            # would read as "the summariser failed, carry on" and the ceiling
            # would stop nothing.
            self._budget.check(run_context.run_id, profile.budgets, task=COMPACTION_TASK)
            try:
                summary, input_tokens, output_tokens = await self._summarise(anchor, chunk)
            except Exception:
                # Deliberately broad: the summariser is a model call and can fail
                # in every way a provider can. Whatever it was, the answer is the
                # same — leave the history intact and let the turn carry on. A
                # chunk failing part-way abandons the whole compaction: a summary
                # of only the earlier chunks would silently drop the later ones.
                # `CancelledError` is a BaseException and passes straight
                # through, so a closed tab still stops the run.
                logger.warning(
                    "context compaction skipped: summariser failed; history left intact",
                    exc_info=True,
                )
                return None
            # Recorded the moment it is known, per call, before the audit: the
            # tokens were spent whether or not the compaction goes ahead.
            self._budget.record(
                run_context.run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=route_cost(summary_route, input_tokens, output_tokens),
            )
            calls += 1
            anchor = summary

        try:
            await self._record(run_context, removed, preserved, anchor, calls)
        except Exception:
            # The invariant this class exists for: no record, no removal. The
            # summary is thrown away and the history stays, so the next step can
            # try again and nothing was lost without a trace.
            logger.error(
                "context compaction abandoned: could not record it, history left intact",
                exc_info=True,
            )
            return None

        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *self._build_new_messages(anchor),
                *preserved,
            ]
        }

    def before_model(self, state: Any, runtime: Runtime[RunContext]) -> dict[str, Any] | None:  # type: ignore[override]
        """Refuse the synchronous path rather than silently take the library's.

        The platform runs agents asynchronously. Inheriting the sync hook would
        compact with none of the fixes above — destroying history unrecorded and
        unbounded — and nothing would say so. A clear refusal is the safe failure.
        """
        raise NotImplementedError(
            "platform context compaction is async-only; invoke the agent with ainvoke/astream"
        )

    def _render(self, *, anchor: str, messages: str) -> str:
        if anchor:
            return self._update_prompt.format(summary=anchor, messages=messages).rstrip()
        return self.summary_prompt.format(messages=messages).rstrip()

    def _tokens(self, text: str) -> int:
        """Counted the way the middleware counts everything, so the budget and
        the trigger agree on what a token is."""
        return int(self.token_counter([HumanMessage(content=text)]))

    def _unit_tokens(self, unit: list[AnyMessage]) -> int:
        # Each unit counted on its own errs high, never low: every count rounds
        # up and adds a per-message overhead the joined text pays once.
        return self._tokens(get_buffer_string(unit, format="xml") + "\n")

    async def _summarise(self, anchor: str, chunk: list[AnyMessage]) -> tuple[str, int, int]:
        """One chunk's summary, updating the anchor, and what it cost.

        The library's own summary call discards the response once it has the
        text, and with it the token counts the ceiling needs.
        """
        response = await self._summary_model.ainvoke(
            self._render(anchor=anchor, messages=get_buffer_string(chunk, format="xml")),
            config={"metadata": {"lc_source": "summarization", **internal_call_metadata()}},
        )
        usage = getattr(response, "usage_metadata", None) or {}
        return (
            response.text.strip(),
            int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)),
        )

    async def _record(
        self,
        run_context: RunContext,
        removed: list[AnyMessage],
        preserved: list[AnyMessage],
        summary: str,
        calls: int,
    ) -> None:
        """One audit event per compaction, written before anything is removed.

        The digest of what was removed is what makes the event worth keeping: it
        proves which messages left the run's live state, and lets any copy kept
        elsewhere be shown to be the thing that was compacted. The text leaves
        the NEW checkpoint only — earlier checkpoints of the thread still hold it
        verbatim until the worker's retention lane prunes them. The summary is
        stored as a digest too, not verbatim — it is already in the run's state,
        and an audit row is not the place for a transcript.
        """
        event = AuditEvent(
            id=self._ids.new_uuid(),
            tenant_id=TenantId(run_context.tenant_id),
            workspace_id=WorkspaceId(run_context.workspace_id),
            actor_id=UserId(run_context.actor_id),
            action=COMPACTED_ACTION,
            resource_type="worker_run",
            resource_id=str(run_context.run_id),
            run_id=run_context.run_id,
            trace_id=run_context.trace_id,
            details={
                "removed_messages": len(removed),
                "kept_messages": len(preserved),
                "removed_digest": _digest(removed),
                "summary_digest": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                "summary_chars": len(summary),
                "summary_calls": calls,
                "copy_version": self._copy.version,
            },
            occurred_at=self._clock.now().astimezone(UTC),
        )
        async with self._uow_factory(access_context_from_run(run_context)) as uow:
            await uow.audit.append(event)
            await uow.commit()


def _digest(messages: list[AnyMessage]) -> str:
    """A stable fingerprint of exactly the messages that were removed.

    Over type, id and content, canonically serialised: the same history always
    digests the same, and any change to what was removed changes it.
    """
    canonical = json.dumps(
        [[type(m).__name__, m.id, m.content] for m in messages],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _input_budget(route: ModelRoute, profile_id: str) -> int:
    """The summary route's input budget, or a refusal: never a number nobody chose."""
    if route.max_input_tokens is None:
        raise ConfigError(
            "the summary model route declares no max_input_tokens",
            details={"profile_id": profile_id},
        )
    return route.max_input_tokens


def _is_summary(message: AnyMessage) -> bool:
    return bool(message.additional_kwargs.get("dw_system_generated"))


def _anchor_and_rest(removed: list[AnyMessage]) -> tuple[str, list[AnyMessage]]:
    """The previous summary's text, and everything else that is being removed.

    The anchor is found by the marker `_build_new_messages` sets, not inferred
    from position or wording. Should there be more than one, the latest is the
    one that already folded in the others.
    """
    summaries = [m for m in removed if _is_summary(m)]
    anchor = str(summaries[-1].content) if summaries else ""
    return anchor, [m for m in removed if not _is_summary(m)]


def _units(messages: list[AnyMessage]) -> Iterator[list[AnyMessage]]:
    """Messages grouped so no chunk boundary falls between a tool call and its results."""
    i = 0
    while i < len(messages):
        head = messages[i]
        unit = [head]
        i += 1
        if isinstance(head, AIMessage) and head.tool_calls:
            ids = {call.get("id") for call in head.tool_calls}
            while i < len(messages):
                follower = messages[i]
                if not (isinstance(follower, ToolMessage) and follower.tool_call_id in ids):
                    break
                unit.append(follower)
                i += 1
        yield unit
