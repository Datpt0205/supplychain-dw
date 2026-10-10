"""The pre-production test, measured and judged like a sample round (step 12,
R&D; ticket ai-automation/17, item 2).

R&D measures the pre-production sample against the same criteria a sample
round uses (the tenant's `supply_chain_sample_criteria` for the product's
Category); code compares (`sample_evaluation.judge`); the model only words the
record's notes. What this module adds to that:

- **Which attempt** (`attempt_of`): a failed test can be taken again with a
  new report, so values belong to an attempt: the first, plus one per failed
  test the case's history records. Values of an earlier attempt never judge a
  later one.
- **What code suggests** beside the empty pass / fail choice
  (`suggested_test_action`): fail when a criterion fails, pass when every one
  passed, nothing while one is unmeasured. A suggestion only, never the step.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from dw_supply_chain.domain.packaging_design import PackagingAction, PackagingHistoryEntry
from dw_supply_chain.domain.sample_evaluation import CriterionResult, suggested_outcome

TEST_ACTIONS = frozenset(
    {PackagingAction.PASS_PRE_PRODUCTION_TEST, PackagingAction.FAIL_PRE_PRODUCTION_TEST}
)

_SUGGESTED = {
    "pass": PackagingAction.PASS_PRE_PRODUCTION_TEST,
    "revise": PackagingAction.FAIL_PRE_PRODUCTION_TEST,
}


def attempt_of(history: Iterable[PackagingHistoryEntry]) -> int:
    return 1 + sum(1 for e in history if e.action is PackagingAction.FAIL_PRE_PRODUCTION_TEST)


def suggested_test_action(results: Sequence[CriterionResult]) -> PackagingAction | None:
    outcome = suggested_outcome(results)
    return None if outcome is None else _SUGGESTED[outcome]


__all__ = ["TEST_ACTIONS", "attempt_of", "suggested_test_action"]
