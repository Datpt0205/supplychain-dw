"""The approval matrix's own parsing/lookup rules, plus a real load of the
shipped file. `AdvancePOCase` (`application/handlers.py`) is the real caller;
this file stays scoped to the policy's own schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from dw_supply_chain.approval_matrix import (
    SupplyChainApprovalMatrix,
    load_supply_chain_approval_matrix,
)
from dw_supply_chain.domain.po_case import CaseAction

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
MATRIX_PATH = REPO_ROOT / "configs" / "policies" / "supply_chain_approval_matrix@1.0.0.yaml"


def _matrix(**overrides: object) -> SupplyChainApprovalMatrix:
    defaults: dict[str, object] = {
        "schema_version": "1.0",
        "policy_id": "supply_chain_approval_matrix",
        "policy_version": "1.0.0",
    }
    defaults.update(overrides)
    return SupplyChainApprovalMatrix(**defaults)


def test_the_shipped_matrix_file_parses_and_ships_empty() -> None:
    """The real committed file, not a fixture — the platform default must
    ship with nothing gated until a tenant opts in."""
    matrix = load_supply_chain_approval_matrix(MATRIX_PATH)
    assert matrix.policy_id == "supply_chain_approval_matrix"
    assert matrix.approval_required_actions == frozenset()


def test_an_action_in_the_set_requires_approval() -> None:
    matrix = _matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))
    assert matrix.requires_approval(CaseAction.CANCEL) is True


def test_an_action_not_in_the_set_does_not_require_approval() -> None:
    matrix = _matrix(approval_required_actions=frozenset({CaseAction.CANCEL}))
    assert matrix.requires_approval(CaseAction.REQUEST_DEPOSIT) is False


def test_the_default_matrix_requires_approval_for_nothing() -> None:
    matrix = _matrix()
    assert all(not matrix.requires_approval(action) for action in CaseAction)


def test_an_unrecognised_action_name_is_refused_by_the_schema() -> None:
    """A typo'd action name is refused at write time, never a config entry
    that reads like a safeguard and gates nothing."""
    with pytest.raises(ValidationError):
        SupplyChainApprovalMatrix(
            schema_version="1.0",
            policy_id="supply_chain_approval_matrix",
            policy_version="1.0.0",
            approval_required_actions=frozenset({"cancle"}),
        )


def test_an_unversioned_or_malformed_file_is_refused_not_guessed() -> None:
    raw = yaml.safe_load(MATRIX_PATH.read_text(encoding="utf-8"))
    del raw["policy_version"]
    with pytest.raises(ValidationError):
        SupplyChainApprovalMatrix.model_validate(raw)
