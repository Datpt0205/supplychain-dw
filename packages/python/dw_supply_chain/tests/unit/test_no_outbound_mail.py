"""Architecture: Supply Chain sends nothing to a supplier (ADR 0029, E18;
ticket ai-automation/07). AI drafts, a person copies and sends from their own
mailbox: no module of the context imports a mail client, and no tool it ships
has an `external` side effect. A mailbox or an SMTP adapter is a new ADR, not
an import."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[5]
SOURCE = REPO_ROOT / "packages" / "python" / "dw_supply_chain" / "src"
_MAIL = ("smtplib", "imaplib", "poplib", "aiosmtplib", "aioimaplib", "sendgrid", "mailgun")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_the_walk_sees_the_supplier_message_module() -> None:
    walked = {p.name for p in SOURCE.rglob("*.py")}
    assert "supplier_messages.py" in walked


def test_no_supply_chain_module_imports_a_mail_client() -> None:
    offending = sorted(
        f"{path.relative_to(SOURCE)}: {name}"
        for path in SOURCE.rglob("*.py")
        for name in _imports(path)
        if name in _MAIL
    )
    assert offending == []


def test_no_supply_chain_tool_has_an_external_side_effect() -> None:
    external = [
        path.name
        for path in (REPO_ROOT / "configs" / "tools").rglob("*.yaml")
        if "supply_chain" in path.name
        and (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("side_effect_level")
        == "external"
    ]
    assert external == []
