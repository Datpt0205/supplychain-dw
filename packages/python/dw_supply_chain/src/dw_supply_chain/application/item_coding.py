"""Step 9 prepared by code (ticket ai-automation/13; ADR 0018, ADR 0027).

What the step-9 recipe, check, subject and applier read, and nothing a model
says: the tenant's code rule (`supply_chain_item_code_rule`), the codes the
application and the imported catalogue already hold, and the case's latest
BM04 version (its `variants`).

- **`ItemCodingPreparation.facts`**: the item code to propose (the case's own
  when it has one, else the rule's next, else none: no rule, no code) and one
  SKU per BM04 variant under it (the case's own SKUs when it has them), each
  code from the rule, each planned quantity only when the BM04's line wrote
  one. Under the caller's tenant AND workspace: another workspace's BM04 is
  "no BM04", another workspace's codes are not seen (the database's UNIQUE,
  tenant-wide, still refuses one when it is written).
- **`ItemCodingPreparation.taken`**: which of a paper's codes the application
  (another case's) or the catalogue already holds. The check names each; the
  proposal's subject binds the answer, so a code taken after the proposal was
  raised supersedes the decision; and the approval asks again before writing.
- **The policy's GET and PUT** (`action_duties.read|write`), as every process
  rule of the tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from dw_kernel.errors import ConflictError, DomainError
from dw_kernel.ports import IdGenerator, UtcClock
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort, PolicyOverridePort
from dw_supply_chain.application.bm04_prefill import Bm04ProfileReadPort
from dw_supply_chain.application.handlers import (
    ACTION_DUTIES_READ,
    ACTION_DUTIES_WRITE,
    _put_policy_override,
)
from dw_supply_chain.domain.document_draft import field_value
from dw_supply_chain.domain.item_coding import (
    ItemCodeRule,
    TakenCodes,
    bm04_variants,
    next_item_code,
    taken_message,
)
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.item_code_rule_policy import (
    ITEM_CODE_RULE_POLICY_ID,
    SupplyChainItemCodeRule,
    resolve_item_code_rule,
)

_POLICY_RESOURCE = "item_code_rule"


class CodeRegistryPort(Protocol):
    """The codes a workspace already holds (RLS), in the application and in
    the imported catalogue."""

    async def item_codes_with_prefix(self, context: AccessContext, prefix: str) -> list[str]: ...

    async def taken(
        self,
        context: AccessContext,
        item_codes: Sequence[str],
        sku_codes: Sequence[str],
        *,
        except_case: uuid.UUID,
    ) -> TakenCodes:
        """Which of these codes are held, and where: another case's in the
        application (`except_case`'s own are its, not taken), or the
        catalogue's."""
        ...


@dataclass(frozen=True, slots=True)
class SkuProposal:
    sku_code: str | None
    variant_label: str
    planned_quantity: int | None


@dataclass(frozen=True, slots=True)
class ItemCodingFacts:
    """What step 9's paper is made from."""

    rule: ItemCodeRule | None
    item_code: str | None
    # True when the code is the case's own (issued before), not a proposal.
    item_code_issued: bool
    skus: tuple[SkuProposal, ...]
    # The BM04 version the variants came from (None: the case has none).
    profile_id: str | None
    material: str | None
    dimensions: str | None


def paper_codes(fields: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """The item code and the SKU codes a step-9 paper holds now (a person may
    have edited them)."""
    item = field_value(fields, "item_code")
    rows = field_value(fields, "skus")
    skus = [
        str(row["sku_code"]).strip()
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, Mapping)
        and isinstance(row.get("sku_code"), str)
        and row["sku_code"].strip()
    ]
    return (item.strip() if isinstance(item, str) and item.strip() else None), skus


def coding_digest(profile_id: str | None, taken: TakenCodes) -> str:
    """What a step-9 proposal's subject binds besides its paper: the BM04
    version its SKUs came from and which of its codes were taken."""
    return f"coding:{profile_id or ''}:{taken.digest()}"


@dataclass(frozen=True)
class ItemCodingPreparation:
    codes: CodeRegistryPort
    profiles: Bm04ProfileReadPort
    policy_override_repo: PolicyOverridePort
    platform_default_rule: SupplyChainItemCodeRule

    async def facts(self, context: AccessContext, case: ProductDevelopmentCase) -> ItemCodingFacts:
        rule = (
            await resolve_item_code_rule(
                context, self.policy_override_repo, self.platform_default_rule
            )
        ).rule
        profile = await self.profiles.latest(context, case.id.value)
        attributes = {} if profile is None else dict(profile.attributes)

        def text(name: str) -> str | None:
            value = attributes.get(name)
            return value.strip() if isinstance(value, str) and value.strip() else None

        if case.item_code is not None:
            item_code: str | None = case.item_code.code
        elif rule is not None:
            prefix = f"{rule.prefix}{rule.separator}"
            item_code = next_item_code(
                rule, await self.codes.item_codes_with_prefix(context, prefix)
            )
        else:
            item_code = None
        if case.skus:
            skus = tuple(
                SkuProposal(s.sku_code, s.variant_label, s.planned_quantity) for s in case.skus
            )
        else:
            skus = tuple(
                SkuProposal(
                    None if rule is None or item_code is None else rule.sku_code(item_code, index),
                    variant.label,
                    variant.planned_quantity,
                )
                for index, variant in enumerate(bm04_variants(text("variants")), start=1)
            )
        return ItemCodingFacts(
            rule=rule,
            item_code=item_code,
            item_code_issued=case.item_code is not None,
            skus=skus,
            profile_id=None if profile is None else str(profile.id),
            material=text("material"),
            dimensions=text("dimensions"),
        )

    async def taken(
        self, context: AccessContext, case: ProductDevelopmentCase, fields: Mapping[str, Any]
    ) -> TakenCodes:
        item, skus = paper_codes(fields)
        return await self.codes.taken(
            context, [item] if item else [], skus, except_case=case.id.value
        )

    async def refuse_taken(
        self, context: AccessContext, case: ProductDevelopmentCase, fields: Mapping[str, Any]
    ) -> None:
        """At the approval, before anything is written: a code another case
        or the catalogue holds is refused (409), naming it."""
        taken = await self.taken(context, case, fields)
        messages = [taken_message("Mã hàng", c, h) for c, h in taken.item_codes.items()] + [
            taken_message("Mã SKU", c, h) for c, h in taken.sku_codes.items()
        ]
        if messages:
            raise ConflictError(
                "; ".join(messages),
                details={
                    "item_codes": sorted(taken.item_codes),
                    "sku_codes": sorted(taken.sku_codes),
                },
            )


def coding_rows(fields: Mapping[str, Any]) -> tuple[str, list[SkuProposal]]:
    """The item code and SKUs an approved step-9 paper issues, each checked:
    a paper without an item code, or with a SKU without a code or a variant,
    is refused (422) before anything is written."""
    item, _ = paper_codes(fields)
    if item is None:
        raise DomainError(
            "phiếu mã hàng chưa có mã hàng; nhập mã trong bản nháp rồi duyệt",
            details={"field": "item_code"},
        )
    rows = field_value(fields, "skus")
    skus: list[SkuProposal] = []
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("sku_code") or "").strip()
        label = str(row.get("variant_label") or "").strip()
        if not code or not label:
            raise DomainError("mỗi SKU cần mã và biến thể", details={"field": f"skus[{index}]"})
        raw = row.get("planned_quantity")
        quantity = None
        if raw not in (None, ""):
            try:
                quantity = int(str(raw))
            except ValueError as exc:
                raise DomainError(
                    "số lượng dự kiến phải là số nguyên",
                    details={"field": f"skus[{index}].planned_quantity"},
                ) from exc
        skus.append(SkuProposal(code, label, quantity))
    if not skus:
        raise DomainError("phiếu mã hàng chưa có SKU nào", details={"field": "skus"})
    return item, skus


# ---------------------------------------------------------------- policy ---


@dataclass(frozen=True)
class GetItemCodeRulePolicy:
    policy_override_repo: PolicyOverridePort
    platform_default_rule: SupplyChainItemCodeRule
    authz: AuthorizationPort

    async def handle(self, context: AccessContext) -> SupplyChainItemCodeRule:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_READ, resource_type=_POLICY_RESOURCE
        )
        return await resolve_item_code_rule(
            context, self.policy_override_repo, self.platform_default_rule
        )


@dataclass(frozen=True)
class SetItemCodeRulePolicyOverride:
    policy_override_repo: PolicyOverridePort
    authz: AuthorizationPort
    ids: IdGenerator
    clock: UtcClock

    async def handle(self, context: AccessContext, policy: SupplyChainItemCodeRule) -> None:
        await self.authz.require(
            context=context, action=ACTION_DUTIES_WRITE, resource_type=_POLICY_RESOURCE
        )
        await _put_policy_override(
            context,
            self.policy_override_repo,
            policy_id=ITEM_CODE_RULE_POLICY_ID,
            policy=policy,
            resource_type=_POLICY_RESOURCE,
            ids=self.ids,
            clock=self.clock,
        )


__all__ = [
    "CodeRegistryPort",
    "GetItemCodeRulePolicy",
    "ItemCodingFacts",
    "ItemCodingPreparation",
    "SetItemCodeRulePolicyOverride",
    "SkuProposal",
    "coding_digest",
    "coding_rows",
    "paper_codes",
]
