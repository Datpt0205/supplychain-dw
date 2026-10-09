"""What a case may say when it leaves the portal through a chat (ADR 0026, E15;
QE-20 provisional).

Zalo's servers carry every reply, so what a reply may name is a list, and this
module is its one owner: a reply is built from these views, never from a case
itself. A view holds identifiers, names and the state; no price, amount,
payment term or bank detail (`dw_supply_chain.domain.commercial.PRICE_FIELDS`),
and `test_zalo_views` fails the day a field of that list is added here.

`ZALO_FIELDS` is the list, derived from the views' own fields so the two cannot
disagree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, fields

from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
)


@dataclass(frozen=True, slots=True)
class ZaloPOCaseView:
    id: uuid.UUID
    po_reference: str | None
    supplier_name: str
    state: CaseState


@dataclass(frozen=True, slots=True)
class ZaloProductCaseView:
    id: uuid.UUID
    proposal_code: str
    product_name: str
    state: ProductDevState


ZALO_FIELDS: frozenset[str] = frozenset(
    f.name for view in (ZaloPOCaseView, ZaloProductCaseView) for f in fields(view)
)


def po_case_view(case: POCase) -> ZaloPOCaseView:
    return ZaloPOCaseView(
        id=case.id.value,
        po_reference=case.po_reference,
        supplier_name=case.supplier_name,
        state=case.state,
    )


def product_case_view(case: ProductDevelopmentCase) -> ZaloProductCaseView:
    return ZaloProductCaseView(
        id=case.id.value,
        proposal_code=case.proposal_code,
        product_name=case.product_name,
        state=case.state,
    )
