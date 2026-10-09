"""The templates of a message to a supplier (ADR 0029; ticket ai-automation/07).

`configs/policies/supply_chain_supplier_messages@1.0.0.yaml`: a greeting, a
reply-by line and a closing, and per purpose a subject and a reply window. A
tenant's own version replaces it whole through `PolicyOverridePort`, falling
back to the platform's, never to another tenant's. Every purpose has a
template, checked when the document loads, so a purpose the lane may draft is
never one nobody can word. Placed at the package's top level, like
`step_preparation_policy.py`: a versioned artifact's schema and parser.
"""

from __future__ import annotations

import string
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import PolicyOverridePort
from dw_supply_chain.domain.supplier_message import MessagePurpose

__all__ = [
    "SUPPLIER_MESSAGES_POLICY_ID",
    "MessageTemplate",
    "SupplyChainSupplierMessages",
    "load_supply_chain_supplier_messages",
    "resolve_supplier_messages",
]

SUPPLIER_MESSAGES_POLICY_ID = "supply_chain_supplier_messages"
_PLACEHOLDERS = frozenset({"reference", "product", "recipient", "reply_by"})


def _check_placeholders(text: str, where: str) -> None:
    names = {name for _, name, _, _ in string.Formatter().parse(text) if name}
    unknown = sorted(names - _PLACEHOLDERS)
    if unknown:
        raise ValueError(f"{where}: unknown placeholders {unknown}")


class MessageTemplate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    reply_within_days: int = Field(ge=1, le=60)


class SupplyChainSupplierMessages(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    greeting: str = Field(min_length=1, max_length=200)
    closing: str = Field(min_length=1, max_length=200)
    reply_line: str = Field(min_length=1, max_length=300)
    templates: dict[MessagePurpose, MessageTemplate]

    @model_validator(mode="after")
    def _every_purpose_worded(self) -> SupplyChainSupplierMessages:
        missing = sorted(p.value for p in MessagePurpose if p not in self.templates)
        if missing:
            raise ValueError(f"purposes without a template: {missing}")
        _check_placeholders(self.greeting, "greeting")
        _check_placeholders(self.closing, "closing")
        _check_placeholders(self.reply_line, "reply_line")
        for purpose, template in self.templates.items():
            _check_placeholders(template.subject, f"{purpose.value}.subject")
        return self


def load_supply_chain_supplier_messages(path: Path) -> SupplyChainSupplierMessages:
    return SupplyChainSupplierMessages.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


async def resolve_supplier_messages(
    context: AccessContext,
    policy_override_repo: PolicyOverridePort,
    platform_default: SupplyChainSupplierMessages,
) -> SupplyChainSupplierMessages:
    """The tenant's own templates if it set them (re-validated whole), the
    platform's otherwise."""
    override = await policy_override_repo.get(context, SUPPLIER_MESSAGES_POLICY_ID)
    if override is None:
        return platform_default
    return SupplyChainSupplierMessages.model_validate(override)
