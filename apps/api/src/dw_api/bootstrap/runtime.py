"""Agent-runtime wiring: registries, model gateway, tool executor, workflow runner.

This is the half of the composition root a bounded context actually plugs into.
Nothing here names a context; what it produces is the ``RuntimeSeam`` a context's
``register_*`` function is later called with.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.checkpoint import SqlAlchemyCheckpointSaver
from dw_agent_runtime.adapters.langchain_usage import LangchainUsageMeter
from dw_agent_runtime.adapters.langgraph_runner import LangGraphWorkflowRunner
from dw_agent_runtime.adapters.model_stack import ModelStack, build_model_stack
from dw_agent_runtime.adapters.run_store import SqlWorkerRunStore
from dw_agent_runtime.adapters.spend_guard import SqlSpendGuardStore
from dw_agent_runtime.adapters.tool_execution_store import SqlToolExecutionStore
from dw_agent_runtime.approval_flow import ApproveAndResumeService
from dw_agent_runtime.autonomy import AutonomyApprovalPolicy
from dw_agent_runtime.executor import ToolExecutor
from dw_agent_runtime.model.copy import load_runtime_copy
from dw_agent_runtime.model.profiles import ModelProfileRegistry
from dw_agent_runtime.model.prompts import load_shipped_prompts
from dw_agent_runtime.ports import RunAllowancePort
from dw_agent_runtime.registry import GraphRegistry, WorkerRegistry
from dw_agent_runtime.tool_specs import ToolSpecRegistry
from dw_agent_runtime.tools import ToolRegistry
from dw_agent_runtime.toolsets import ToolsetRegistry
from dw_api.adapters.document_indexing import KnowledgeDocumentIndexingAdapter
from dw_api.bootstrap.container import RuntimeSeam
from dw_api.bootstrap.knowledge import build_embeddings, build_reranker, build_vector_index
from dw_api.bootstrap.models import build_chat_model_factory, model_provider_config
from dw_api.bootstrap.paths import (
    ATTACHMENT_POLICY,
    MOCK_MODEL_FIXTURES,
    MODEL_PROFILES_DIR,
    PROMPTS_DIR,
    RUNTIME_COPY_CONFIG,
    TOOL_SPECS_DIR,
    TOOLSETS_DIR,
    release_manifest_ref,
)
from dw_api.settings import ApiSettings
from dw_kernel.ports import IdGenerator, UtcClock
from dw_knowledge.adapters.evidence_store import SqlEvidenceStore
from dw_knowledge.attachment_policy import load_attachment_policy
from dw_knowledge.gateway import KnowledgeGateway
from dw_knowledge.ingest_jobs import IngestJobStore
from dw_knowledge.ports import ObjectStoragePort
from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker
from dw_memory.policy import MemoryWritePolicy
from dw_memory.review import MEMORY_REVIEW, require_clearance_for_review
from dw_memory.service import MemoryService
from dw_observability.telemetry import TelemetryPort
from dw_platform.application.ports import PlatformUnitOfWorkFactory


@dataclass(frozen=True)
class RuntimeWiring:
    """Everything the runtime half produces, in one return value."""

    seam: RuntimeSeam
    runner: LangGraphWorkflowRunner
    approval_flow: ApproveAndResumeService
    knowledge_gateway: KnowledgeGateway
    ingest_jobs: IngestJobStore
    document_indexing: KnowledgeDocumentIndexingAdapter
    memory_service: MemoryService
    tool_registry: ToolRegistry
    # The gateway, ledger and recorders, as the shared builder made them; a
    # context's one-call handlers take `model_stack.one_call(allowance)`.
    model_stack: ModelStack


def _build_memory_ranker(
    settings: ApiSettings, profiles: ModelProfileRegistry
) -> QdrantMemoryRanker | None:
    """The memory ranker, when this deployment has a vector store to rank with.

    `None` rather than a stub: a stub that returned an empty order would look
    like "the ranker has no opinion about any of these", which is a real answer
    and not the same as "there is no ranker". `MemoryService` branches on the
    absence and never asks.

    Imported inside the function, like the knowledge index is: a deployment
    without Qdrant should not need the client installed to boot.
    """
    if not settings.qdrant_url:
        return None
    from qdrant_client import AsyncQdrantClient

    from dw_memory.adapters.qdrant_ranker import QdrantMemoryRanker as _Ranker

    return _Ranker(
        client=AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key),
        embedder=build_embeddings(settings, profiles),
    )


def build_runtime(
    settings: ApiSettings,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    uow_factory: PlatformUnitOfWorkFactory,
    run_store: SqlWorkerRunStore,
    allowance: RunAllowancePort,
    object_storage: ObjectStoragePort,
    telemetry: TelemetryPort,
    clock: UtcClock,
    ids: IdGenerator,
) -> RuntimeWiring:
    # ---- versioned artifact registries -----------------------------------
    profiles = ModelProfileRegistry()
    profiles.load_directory(MODEL_PROFILES_DIR)
    # Prompts and the skills they declare, checked against each other.
    prompts = load_shipped_prompts(PROMPTS_DIR.parent)
    copy = load_runtime_copy(RUNTIME_COPY_CONFIG)

    # ---- model gateway ---------------------------------------------------
    # Built by the one builder the worker uses too (`build_model_stack`): the
    # gateway, ONE per-run spend ledger for the whole process — shared by the
    # structured gateway, every agent's budget middleware (on the seam, below)
    # and the runner that frees a run's entry when it ends — and the usage
    # recorders (the daily spend guard and telemetry). Two ledgers would split
    # a run's spend so neither half reaches the ceiling.
    model_stack = build_model_stack(
        model_provider_config(settings),
        profiles=profiles,
        prompts=prompts,
        session_factory=session_factory,
        clock=clock,
        telemetry=telemetry,
        mock_fixtures_dir=MOCK_MODEL_FIXTURES,
    )
    budget = model_stack.budget
    gateway = model_stack.gateway
    usage_recorder = model_stack.usage_recorder
    # The LangChain path (agent loops, structured output) bills into the same
    # ledger through this meter rather than going unmetered.
    usage_meter = LangchainUsageMeter(profiles=profiles, recorder=usage_recorder)
    chat_models = build_chat_model_factory(settings, profiles, copy)

    # ---- retrieval -------------------------------------------------------
    knowledge_gateway = KnowledgeGateway(
        session_factory=session_factory,
        vector_index=build_vector_index(settings),
        embeddings=build_embeddings(settings, profiles),
        object_storage=object_storage,
        clock=clock,
        id_generator=ids,
        reranker=build_reranker(settings),
        telemetry=telemetry,
    )
    # Upload path: the API stages the raw file and enqueues; the worker ingests.
    ingest_jobs = IngestJobStore(session_factory=session_factory, clock=clock, id_generator=ids)
    document_indexing = KnowledgeDocumentIndexingAdapter(
        jobs=ingest_jobs, policy=load_attachment_policy(ATTACHMENT_POLICY)
    )

    # ---- tools -----------------------------------------------------------
    # A context registers its tool factories on `tools`; the policy each one
    # runs under (scopes, side-effect level, approval, idempotency, timeout)
    # comes from its spec in configs/tools, never from the factory.
    tool_registry = ToolRegistry()
    # ONE approval policy for the process: the executor decides with it, the
    # runner stamps its version on every run, and every agent's middleware reads
    # it through the executor. Two instances could carry two versions, and a run
    # stamped under one would be decided under the other.
    approval_policy = AutonomyApprovalPolicy()
    tool_executor = ToolExecutor(
        registry=tool_registry,
        execution_store=SqlToolExecutionStore(session_factory),
        uow_factory=uow_factory,
        clock=clock,
        id_generator=ids,
        approval_policy=approval_policy,
        telemetry=telemetry,
    )
    tool_specs = ToolSpecRegistry(copy=copy)
    tool_specs.load_directory(TOOL_SPECS_DIR)
    toolsets = ToolsetRegistry()
    toolsets.load_directory(TOOLSETS_DIR)

    # ---- memory ----------------------------------------------------------
    memory_service = MemoryService(
        session_factory=session_factory,
        policy=MemoryWritePolicy(),
        clock=clock,
        id_generator=ids,
        # Knowledge owns the chunks a citation is checked against; memory owns
        # whether a fact is kept. Neither imports the other's tables — the port is
        # declared by memory and satisfied here.
        evidence_store=SqlEvidenceStore(clock=clock),
        # Orders a long list of recalled facts by what the turn is about. Absent
        # without Qdrant, and absence costs only the ordering: recall still
        # answers, confidence-first, exactly as it did before ranking existed.
        ranker=_build_memory_ranker(settings, profiles),
    )

    # ---- graphs, workers, runner ----------------------------------------
    # A context calls its `register_*_graphs(graphs, services)` on the seam,
    # then `workers.load_file(...)` for the worker YAML whose graph it just
    # registered. A process loads only the workers it hosts: configs/workers is
    # shared, and loading a file whose graph lives in another process would fail
    # fast for the wrong reason.
    graphs = GraphRegistry()
    workers = WorkerRegistry(graph_registry=graphs)
    runner = LangGraphWorkflowRunner(
        worker_registry=workers,
        graph_registry=graphs,
        checkpoint_saver=SqlAlchemyCheckpointSaver(session_factory),
        run_store=run_store,
        uow_factory=uow_factory,
        clock=clock,
        id_generator=ids,
        allowance=allowance,
        budget=budget,
        approval_policy=approval_policy,
        release_manifest_ref=release_manifest_ref(),
        telemetry=telemetry,
        usage_meter=usage_meter,
        spend_store=SqlSpendGuardStore(session_factory=session_factory),
    )
    approval_flow = ApproveAndResumeService(
        uow_factory=uow_factory,
        runner=runner,
        run_store=run_store,
        clock=clock,
        id_generator=ids,
        # A held memory is a fact the system will repeat to everyone in the
        # workspace once approved: the person whose run proposed it may not
        # vouch for it alone, and whoever does says why. A context adds its own
        # prefixes with `|=` where it is wired.
        strict_approval_prefixes=frozenset({"memory."}),
        # And nobody decides on a memory they are not cleared to read.
        decision_guards={MEMORY_REVIEW: require_clearance_for_review},
    )

    seam = RuntimeSeam(
        session_factory=session_factory,
        clock=clock,
        ids=ids,
        telemetry=telemetry,
        profiles=profiles,
        prompts=prompts,
        copy=copy,
        gateway=gateway,
        chat_models=chat_models,
        usage_meter=usage_meter,
        budget=budget,
        tools=tool_registry,
        tool_executor=tool_executor,
        tool_specs=tool_specs,
        toolsets=toolsets,
        graphs=graphs,
        workers=workers,
        knowledge=knowledge_gateway,
        memory=memory_service,
    )
    return RuntimeWiring(
        seam=seam,
        runner=runner,
        approval_flow=approval_flow,
        knowledge_gateway=knowledge_gateway,
        ingest_jobs=ingest_jobs,
        document_indexing=document_indexing,
        memory_service=memory_service,
        tool_registry=tool_registry,
        model_stack=model_stack,
    )
