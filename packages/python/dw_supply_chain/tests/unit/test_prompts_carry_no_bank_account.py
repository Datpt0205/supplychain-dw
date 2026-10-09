"""Unit: no registered prompt has a variable a bank account could travel in
(ADR 0021 amended 2026-10-09, point 5; ADR 0026, E15; ticket ai-automation/01).

Walks every prompt the platform ships and loads it through the registry, so a
prompt added later is covered without editing this file. A bank account is
read and compared by code (`domain.commercial.same_account`); a variable named
for one, or for any of its fields, is refused here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dw_agent_runtime.model.prompts import PromptRegistry
from dw_supply_chain.domain.commercial import BANK_ACCOUNT_FIELDS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
_BANKISH = re.compile(r"bank|account|iban|swift|stk|tai_khoan", re.IGNORECASE)


def _registered() -> list[tuple[str, frozenset[str]]]:
    found: list[tuple[str, frozenset[str]]] = []
    for path in sorted((REPO_ROOT / "configs" / "prompts").rglob("*.yaml")):
        artifact = PromptRegistry().load_file(path)
        found.append((f"{artifact.prompt_id}@{artifact.version}", artifact.variables))
    return found


def test_every_prompt_is_walked() -> None:
    assert len(_registered()) == len(list((REPO_ROOT / "configs" / "prompts").rglob("*.yaml")))
    assert _registered()


def test_no_prompt_variable_can_carry_a_bank_account() -> None:
    offending = [
        (prompt, name)
        for prompt, variables in _registered()
        for name in variables
        if name in BANK_ACCOUNT_FIELDS or _BANKISH.search(name)
    ]
    assert offending == []
