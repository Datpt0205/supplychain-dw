"""The one place a platform agent is assembled.

Every bounded context used to build its own: `create_agent(...)` with whichever
middleware its author remembered. The middlewares in this package each close a
hole that was found in production — a builtin tool that bypassed the executor,
a tool nobody declared passing a scope check by not being declared, a worker
prompt that silently deleted the skills catalogue, a sibling approval nobody was
shown, a tool failure that killed the turn and lost the transcript, a binary
file that made the provider reject the whole request. A context that assembles
its own agent and forgets one reopens that hole, and nothing tells it so.

So assembly is a platform artifact, not a per-context habit.

What is and is not a contract here, measured rather than assumed. All 720
orderings of the first six were run against the properties they protect, and not
one ordering broke any of them: none of these middlewares reads what another one
writes, so their order is free. The seventh, the spend ceiling, was added after
that measurement and was not put through it — it reads only the model's own
response, which none of the others write to. What is NOT free is PRESENCE:
removing any single one of the seven breaks exactly the property it owns. That is
what this factory guarantees and what `test_agent_factory.py` pins.

The obvious candidate for a first real ordering contract was a summariser that
had to see files already stripped. Measured: it is not one — the summariser
serialises history to text before sending it, so no file block reaches it. An
order becomes a contract the day a test shows one breaking, and not before.

Built on `create_agent`, not `create_deep_agent`. Measured on the pinned
deepagents: the deep variant installs eight builtin tools on its own —
`delete`, `edit_file`, `glob`, `grep`, `ls`, `read_file`, `task`, `write_file` —
every one of which reaches outside the run without passing `ToolExecutor`, and
therefore without authorization, idempotency or audit. The set is not even
stable: comments written against an earlier release named `execute` and not
`delete`. Installing a library's tools and then stripping them is a denylist
against a list the library is free to change. `create_agent` installs nothing,
so the platform adds exactly what it controls.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.summarization import ContextSize
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from dw_agent_runtime.adapters.context_compaction import PlatformSummarizationMiddleware
from dw_agent_runtime.adapters.langchain_tools import (
    OfferedToolsOnlyMiddleware,
    OneApprovalPerStepMiddleware,
    PlatformToolErrorsMiddleware,
    ScopedToolsMiddleware,
    UnreadableFilesMiddleware,
    platform_tools,
)
from dw_agent_runtime.adapters.model_retry import ModelRetryMiddleware
from dw_agent_runtime.adapters.recalled_memory import MemoryRecallPort, RecalledMemoryMiddleware
from dw_agent_runtime.adapters.run_budget import RunBudgetMiddleware
from dw_agent_runtime.adapters.sub_agents import SubAgentSpec, sub_agent_middleware
from dw_agent_runtime.adapters.system_prompt import WorkerSystemPrompt
from dw_agent_runtime.contracts import RunContext, ToolDefinition
from dw_agent_runtime.executor import ToolExecutor
from dw_agent_runtime.model.budget import RunBudgetLedger
from dw_agent_runtime.model.copy import RuntimeCopy
from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_agent_runtime.model.prompts import PromptRegistry
from dw_agent_runtime.tools import ToolRegistry
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.ports import PlatformUnitOfWorkFactory

__all__ = ["AgentSpec", "CompactionSpec", "build_agent", "platform_middleware"]


def _utc_now() -> datetime:
    """The default clock for recall. A named function, not a lambda, so the
    default a host gets is greppable."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class CompactionSpec:
    """How a worker that runs long enough to need it compacts its history.

    Optional, unlike the spend ceiling. A ceiling is a safety control every agent
    carries; compaction is a capability that costs a model call and only earns it
    for a run that outgrows its context window. A worker answering one question
    at a time would pay for summaries of conversations that fit.
    """

    # Usually a cheaper model than the worker's own: summarising is not the work.
    model: BaseChatModel
    # Whose price a summary is charged at. The ceiling it counts against is the
    # worker's, from `AgentSpec.profile_id`.
    summary_profile_id: str
    uow_factory: PlatformUnitOfWorkFactory
    clock: UtcClock
    ids: IdGenerator
    # When to compact, and how much recent history to keep verbatim.
    trigger: ContextSize
    keep: ContextSize


@dataclass(frozen=True)
class AgentSpec:
    """Everything one worker's agent is built from.

    `offered` is the resolved toolset — the definitions this worker pins, not
    every tool the registry holds. It is handed to the tool builder and to every
    middleware as one list, which is the point: a scope filter reading the whole
    registry while the tool list reads the toolset is how a tool registered for
    another worker becomes callable here.
    """

    model: BaseChatModel
    offered: Sequence[ToolDefinition]
    registry: ToolRegistry
    executor: ToolExecutor
    copy: RuntimeCopy
    # Prefixes the approval type a gated tool raises, so the approvals screen
    # can route a decision back to the context that owns it.
    approval_type_prefix: str
    # The worker prompt: a registry artifact, pinned. Pass the worker
    # definition's `agent_prompt_id`/`agent_prompt_version` - the pin the runner
    # bills this loop under - rather than naming a version a second time.
    prompt_id: str
    prompt_version: str
    prompts: PromptRegistry
    # Rendered per model call, not once at build: the prompt names today's date
    # and the screen in view, and the agent is compiled once per process.
    prompt_variables: Callable[[ModelRequest[Any]], dict[str, str]]
    # The process's one spend ledger — `RuntimeSeam.budget`, never a fresh one —
    # and the profile whose ceiling and price this worker's calls are held to.
    budget: RunBudgetLedger
    profiles: ModelProfileRegistry
    profile_id: str
    compaction: CompactionSpec | None = None
    # What this worker already learned about the record in view. Optional the
    # same way compaction is: a context that stores no memory wires none, and a
    # run with no `subject_ref` recalls nothing even when it is wired.
    recall: MemoryRecallPort | None = None
    # Narrower agents this worker may delegate to. Empty by default: the `task`
    # tool only exists when a context names something to delegate TO, and an
    # agent that can spawn is a larger surface than one that cannot.
    sub_agents: Sequence[SubAgentSpec] = ()
    # Only read when `recall` is set. Passed rather than taken from a module
    # global so a test can pin the moment a validity window is judged against.
    clock: Callable[[], datetime] | None = None


def platform_middleware(spec: AgentSpec) -> list[AgentMiddleware[Any, Any]]:
    """The middleware stack every platform agent carries.

    Seven are unconditional — the safety controls, each closing a hole found in
    production. Two more are wired only when the spec asks: context compaction,
    and recall of what this worker already learned about the record in view. The
    count in this docstring used to be the contract; it is not, and saying
    "seven" while building eight is how a reader stops trusting the comment.
    What IS the contract is that none of the unconditional ones may be dropped.

    Exposed on its own so a context that must add middleware of its own extends
    this list rather than rebuilding it — rebuilding is exactly how one gets
    dropped.

    Any middleware added to this list must let `GraphBubbleUp` through. Measured:
    a single `awrap_tool_call` that catches `BaseException` and returns a message
    swallows the `interrupt()` an approval raises, and the run carries on as if
    a person had said yes. `PlatformToolErrorsMiddleware` is safe because it
    subclasses `ToolErrorMiddleware`, which re-raises it; a hand-written
    `try/except` is not.
    """
    offered = list(spec.offered)
    stack: list[AgentMiddleware[Any, Any]] = [
        WorkerSystemPrompt(
            spec.prompts, spec.prompt_id, spec.prompt_version, spec.prompt_variables
        ),
        # Directly after the worker prompt: the prompt says what this worker is,
        # and recall appends what it happens to know about the record in view.
        # Both are system-message work, so keeping them adjacent means one place
        # to look when asking what the model was told.
        *(
            [
                RecalledMemoryMiddleware(
                    spec.recall,
                    copy=spec.copy,
                    clock=spec.clock if spec.clock is not None else _utc_now,
                )
            ]
            if spec.recall is not None
            else []
        ),
        UnreadableFilesMiddleware(),
        OfferedToolsOnlyMiddleware(offered),
        ScopedToolsMiddleware(offered),
        # Decides which tools are gated per call from the run's autonomy, not once
        # here from the tool. It used to freeze that set at build time, and an
        # agent is compiled once per process, so one set served every tenant at
        # every level. Handed the executor's own policy so this gate, the tool
        # wrapper and the executor cannot disagree about a call.
        OneApprovalPerStepMiddleware(offered, copy=spec.copy, policy=spec.executor.approval_policy),
        PlatformToolErrorsMiddleware(offered, copy=spec.copy),
        # The only ceiling on the path that can loop. Without it an agent run is
        # bounded by `recursion_limit`, which counts steps and not money.
        RunBudgetMiddleware(spec.budget, spec.profiles, spec.profile_id),
        # Last, so it sits closest to the model: a retry re-runs only the call
        # that failed. A failed attempt produces no response and therefore no
        # usage, so the ceiling above is not charged for one.
        ModelRetryMiddleware(),
    ]
    if spec.sub_agents:
        # Last, and only when asked. It adds the `task` tool, so it changes what
        # the model may do — see `sub_agents` for what the platform re-applies
        # inside a delegated turn, and why none of it is inherited.
        stack.append(sub_agent_middleware(spec, spec.sub_agents))
    if spec.compaction is not None:
        stack.append(
            PlatformSummarizationMiddleware(
                spec.compaction.model,
                copy=spec.copy,
                uow_factory=spec.compaction.uow_factory,
                clock=spec.compaction.clock,
                ids=spec.compaction.ids,
                # The SAME ledger the budget middleware above holds, so a summary
                # and an agent step spend against one ceiling.
                budget=spec.budget,
                profiles=spec.profiles,
                profile_id=spec.profile_id,
                summary_profile_id=spec.compaction.summary_profile_id,
                trigger=spec.compaction.trigger,
                keep=spec.compaction.keep,
            )
        )
    return stack


def build_agent(
    spec: AgentSpec,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: BaseStore | None = None,
) -> Any:
    """Assemble a platform agent from one worker's spec.

    Returns the compiled graph `create_agent` produces. The checkpointer is
    optional because a graph a runner hosts is compiled by the runner, which
    passes its own; one built for a test or a one-shot call has none.
    """
    offered = list(spec.offered)
    return create_agent(
        model=spec.model,
        tools=platform_tools(
            offered,
            spec.registry,
            spec.executor,
            approval_type_prefix=spec.approval_type_prefix,
            copy=spec.copy,
        ),
        middleware=platform_middleware(spec),
        context_schema=RunContext,
        checkpointer=checkpointer,
        store=store,
    )
