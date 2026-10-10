"""Canonical metric names.

Central registry so emitters and dashboards never drift. Labels must stay
low-cardinality: never a user id or raw prompt.
"""

from __future__ import annotations

from typing import Final

DW_RUN_TOTAL: Final = "dw_run_total"  # labels: worker, status
DW_RUN_DURATION_SECONDS: Final = "dw_run_duration_seconds"  # labels: worker
DW_NODE_FAILURE_TOTAL: Final = "dw_node_failure_total"  # labels: worker, node, error_type
DW_TOOL_CALL_TOTAL: Final = "dw_tool_call_total"  # labels: tool, status
DW_APPROVAL_WAIT_SECONDS: Final = "dw_approval_wait_seconds"  # labels: worker, approval_type
DW_MODEL_TOKENS_TOTAL: Final = "dw_model_tokens_total"  # labels: provider, model, direction
DW_MODEL_COST_USD_TOTAL: Final = "dw_model_cost_usd_total"  # labels: worker, provider
DW_RETRIEVAL_HIT_RATE: Final = "dw_retrieval_hit_rate"  # labels: worker
# A search answered in vector order because the reranker failed.
DW_RETRIEVAL_RERANK_SKIPPED_TOTAL: Final = "dw_retrieval_rerank_skipped_total"  # labels: error
DW_HUMAN_INTERVENTION_RATE: Final = "dw_human_intervention_rate"  # labels: worker
DW_TASK_SUCCESS_RATE: Final = "dw_task_success_rate"  # labels: worker

# Ops hardening Phase 5. Gauges (current value, not accumulated — see
# `TelemetryPort.set_gauge`): a Counter cannot represent "how many are
# pending right now" without lying about it on the next tick.
DW_OUTBOX_BACKLOG_SIZE: Final = "dw_outbox_backlog_size"  # no labels
DW_OUTBOX_OLDEST_PENDING_AGE_SECONDS: Final = (
    "dw_outbox_oldest_pending_age_seconds"  # no labels; 0 when the backlog is empty
)
DW_REAPER_REAPED_TOTAL: Final = "dw_reaper_reaped_total"  # labels: queue

ALL_METRICS: Final = (
    DW_RUN_TOTAL,
    DW_RUN_DURATION_SECONDS,
    DW_NODE_FAILURE_TOTAL,
    DW_TOOL_CALL_TOTAL,
    DW_APPROVAL_WAIT_SECONDS,
    DW_MODEL_TOKENS_TOTAL,
    DW_MODEL_COST_USD_TOTAL,
    DW_RETRIEVAL_HIT_RATE,
    DW_RETRIEVAL_RERANK_SKIPPED_TOTAL,
    DW_HUMAN_INTERVENTION_RATE,
    DW_TASK_SUCCESS_RATE,
    DW_OUTBOX_BACKLOG_SIZE,
    DW_OUTBOX_OLDEST_PENDING_AGE_SECONDS,
    DW_REAPER_REAPED_TOTAL,
)
