"""Tenant-aware LangGraph checkpoint saver backed by platform.run_checkpoints.

Checkpoints are the durable working state of a thread. A
one-shot worker has one run per thread; a conversation worker keeps many runs on
the same thread. Rows carry tenant_id and sit behind RLS: the saver sets the
transaction-local tenant context from the runner-provided config before every
statement.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import sqlalchemy as sa
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dw_agent_runtime.adapters.runtime_tables import run_checkpoint_writes, run_checkpoints
from dw_kernel.errors import TenantContextMissingError
from dw_platform.adapters.persistence.tenant_session import TenantScope, tenant_session


def _scope(config: RunnableConfig) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    conf = config.get("configurable", {})
    thread_id = conf.get("thread_id")
    tenant_id = conf.get("tenant_id")
    workspace_id = conf.get("workspace_id")
    if not thread_id or not tenant_id or not workspace_id:
        raise TenantContextMissingError(
            "checkpoint config requires thread_id, tenant_id and workspace_id"
        )
    return (
        uuid.UUID(str(thread_id)),
        uuid.UUID(str(tenant_id)),
        uuid.UUID(str(workspace_id)),
        str(conf.get("checkpoint_ns", "")),
    )


# The primary keys the two upserts below conflict on.
_CHECKPOINT_KEY = ["thread_id", "checkpoint_ns", "checkpoint_id"]
_WRITE_KEY = [*_CHECKPOINT_KEY, "task_id", "idx"]


class SqlAlchemyCheckpointSaver(BaseCheckpointSaver[str]):
    """Async checkpoint persistence with per-transaction RLS context."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(serde=JsonPlusSerializer())
        self._session_factory = session_factory

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id, tenant_id, workspace_id, ns = _scope(config)
        wanted_id = config.get("configurable", {}).get("checkpoint_id")

        scope = TenantScope(tenant_id, workspace_id)
        async with tenant_session(self._session_factory, scope) as session:
            query = (
                sa.select(run_checkpoints)
                .where(
                    run_checkpoints.c.thread_id == thread_id,
                    run_checkpoints.c.checkpoint_ns == ns,
                )
                .order_by(run_checkpoints.c.checkpoint_id.desc())
                .limit(1)
            )
            if wanted_id:
                query = query.where(run_checkpoints.c.checkpoint_id == str(wanted_id))
            row = (await session.execute(query)).first()
            if row is None:
                return None

            writes_rows = (
                await session.execute(
                    sa.select(run_checkpoint_writes)
                    .where(
                        run_checkpoint_writes.c.thread_id == thread_id,
                        run_checkpoint_writes.c.checkpoint_ns == ns,
                        run_checkpoint_writes.c.checkpoint_id == row.checkpoint_id,
                    )
                    .order_by(run_checkpoint_writes.c.task_id, run_checkpoint_writes.c.idx)
                )
            ).all()
        return self._row_to_tuple(row, writes_rows)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        if config is None:
            return
        thread_id, tenant_id, workspace_id, ns = _scope(config)
        scope = TenantScope(tenant_id, workspace_id)
        async with tenant_session(self._session_factory, scope) as session:
            query = (
                sa.select(run_checkpoints)
                .where(
                    run_checkpoints.c.thread_id == thread_id,
                    run_checkpoints.c.checkpoint_ns == ns,
                )
                .order_by(run_checkpoints.c.checkpoint_id.desc())
            )
            if before is not None:
                before_id = before.get("configurable", {}).get("checkpoint_id")
                if before_id:
                    query = query.where(run_checkpoints.c.checkpoint_id < str(before_id))
            if limit is not None:
                query = query.limit(limit)
            rows = (await session.execute(query)).all()
        for row in rows:
            yield self._row_to_tuple(row, [])

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id, tenant_id, workspace_id, ns = _scope(config)
        parent_id = config.get("configurable", {}).get("checkpoint_id")

        type_cp, blob_cp = self.serde.dumps_typed(checkpoint)
        _, blob_md = self.serde.dumps_typed(dict(metadata))

        scope = TenantScope(tenant_id, workspace_id)
        async with tenant_session(self._session_factory, scope) as session:
            statement = sa.dialects.postgresql.insert(run_checkpoints).values(
                thread_id=thread_id,
                checkpoint_ns=ns,
                checkpoint_id=checkpoint["id"],
                parent_checkpoint_id=str(parent_id) if parent_id else None,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                type=type_cp,
                checkpoint=blob_cp,
                metadata=blob_md,
                created_at=sa.func.now(),
            )
            await session.execute(
                # A replayed step re-puts its checkpoint, and what it carries the
                # second time is the state that actually came out of it. The
                # reference saver overwrites; refusing the write froze the thread
                # at whatever the first attempt happened to hold. `created_at`
                # stays put - it records when this checkpoint first appeared.
                statement.on_conflict_do_update(
                    index_elements=_CHECKPOINT_KEY,
                    set_={
                        "parent_checkpoint_id": statement.excluded.parent_checkpoint_id,
                        "type": statement.excluded.type,
                        "checkpoint": statement.excluded.checkpoint,
                        "metadata": statement.excluded.metadata,
                    },
                )
            )

        return cast(
            RunnableConfig,
            {
                "configurable": {
                    "thread_id": str(thread_id),
                    "checkpoint_ns": ns,
                    "checkpoint_id": checkpoint["id"],
                    "tenant_id": str(tenant_id),
                    "workspace_id": str(workspace_id),
                }
            },
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id, tenant_id, workspace_id, ns = _scope(config)
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if not checkpoint_id:
            raise TenantContextMissingError("aput_writes requires checkpoint_id in config")

        scope = TenantScope(tenant_id, workspace_id)
        async with tenant_session(self._session_factory, scope) as session:
            for position, (channel, value) in enumerate(writes):
                # `WRITES_IDX_MAP` reserves negative slots for the special
                # channels, which is what keeps slot 0 free for a task's real
                # output. Enumerating blindly parked `__interrupt__` on slot 0 -
                # exactly where `messages` lands when the approved task re-runs
                # against the same checkpoint - and the insert below then threw
                # that output away as a duplicate. The turn a human approved
                # disappeared from the thread, so the next turn proposed the
                # write all over again.
                idx = WRITES_IDX_MAP.get(channel, position)
                type_w, blob_w = self.serde.dumps_typed(value)
                statement = sa.dialects.postgresql.insert(run_checkpoint_writes).values(
                    thread_id=thread_id,
                    checkpoint_ns=ns,
                    checkpoint_id=str(checkpoint_id),
                    task_id=task_id,
                    idx=idx,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    channel=channel,
                    type=type_w,
                    value=blob_w,
                    task_path=task_path,
                )
                if idx < 0:
                    # A special write states what happened on the latest attempt:
                    # a second decision on the same task replaces the first.
                    statement = statement.on_conflict_do_update(
                        index_elements=_WRITE_KEY,
                        set_={
                            "channel": statement.excluded.channel,
                            "type": statement.excluded.type,
                            "value": statement.excluded.value,
                            "task_path": statement.excluded.task_path,
                        },
                    )
                else:
                    # A regular write already on this slot is the same task's
                    # earlier, identical output. Keep the first.
                    statement = statement.on_conflict_do_nothing()
                await session.execute(statement)

    async def adelete_thread(self, thread_id: str) -> None:
        # The runtime never deletes checkpoints on its own: a thread's rows go
        # by the retention term (`SqlCheckpointRetention`, the worker's
        # `checkpoint_retention` lane) or with the tenant (offboarding). A
        # delete here would skip the check that no run on the thread is still
        # waiting to resume from them.
        raise NotImplementedError(
            "checkpoints are deleted by the worker's checkpoint_retention lane "
            "(SqlCheckpointRetention, configs/policies/retention@*.yaml) and by "
            "tenant offboarding, never by the runtime"
        )

    def _row_to_tuple(self, row: Any, writes_rows: Sequence[Any]) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed((row.type, row.checkpoint))
        metadata = self.serde.loads_typed((row.type, row.metadata))
        parent_config: RunnableConfig | None = None
        if row.parent_checkpoint_id:
            parent_config = cast(
                RunnableConfig,
                {
                    "configurable": {
                        "thread_id": str(row.thread_id),
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                        "tenant_id": str(row.tenant_id),
                        "workspace_id": str(row.workspace_id),
                    }
                },
            )
        return CheckpointTuple(
            config=cast(
                RunnableConfig,
                {
                    "configurable": {
                        "thread_id": str(row.thread_id),
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.checkpoint_id,
                        "tenant_id": str(row.tenant_id),
                        "workspace_id": str(row.workspace_id),
                    }
                },
            ),
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=[
                (w.task_id, w.channel, self.serde.loads_typed((w.type, w.value)))
                for w in writes_rows
            ],
        )
