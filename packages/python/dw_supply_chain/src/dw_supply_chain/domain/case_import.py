"""An open case brought in at its current state (ticket onboarding/02; ADR
0027 decision 3).

What this module owns, for both kinds of case:

- **The history row's words** (`IMPORT_REASON`) and the PO history's action
  (`IMPORT_ACTION`; a product case's is `ProductAction.IMPORT`).
- **Where a PO case may start** (`IMPORTABLE_PO_STATES`): a state a person
  moves on from, never a closed one, an interruption (it needs the state it
  interrupted), or `order_requested` (that is ĐẶT HÀNG's, with its product
  case).
- **`imported_po_case`**: the case, refused when its state or dates are not
  one the import may write. Its one history row is the repository's to write
  (`SqlPOCaseRepository.add_imported`), dated `entered_at`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from dw_kernel.errors import DomainError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.po_case import CaseState, OrderKind, POCase, POCaseId

IMPORT_REASON = "Nạp từ dữ liệu cũ"
IMPORT_ACTION = "import"

IMPORTABLE_PO_STATES = frozenset(
    {
        CaseState.PO_CREATED,
        CaseState.WAITING_DEPOSIT,
        CaseState.DEPOSIT_CONFIRMED,
        CaseState.PRE_PRODUCTION,
        CaseState.PRODUCTION,
        CaseState.QC,
        CaseState.REWORK,
        CaseState.IN_TRANSIT,
        CaseState.ARRIVED_PORT,
        CaseState.WAITING_PAYMENT,
        CaseState.PAYMENT_COMPLETED,
        CaseState.WAREHOUSE_RECEIVING,
    }
)


def check_dates(entered_at: datetime, created_at: datetime, now: datetime) -> None:
    if entered_at > now or created_at > now:
        raise DomainError("ngày khai báo ở tương lai")
    if entered_at < created_at:
        raise DomainError("ngày vào bước trước ngày tạo hồ sơ")


def imported_po_case(
    *,
    id: POCaseId,
    tenant_id: TenantId,
    workspace_id: WorkspaceId,
    po_reference: str,
    supplier_name: str,
    state: CaseState,
    order_kind: OrderKind,
    product_dev_case_id: uuid.UUID | None,
    pic_user_id: uuid.UUID | None,
    category: str | None,
    created_at: datetime,
) -> POCase:
    if state not in IMPORTABLE_PO_STATES:
        raise DomainError("chỉ nạp hồ sơ PO ở bước một người làm tiếp", details={"state": state})
    if product_dev_case_id is not None and (pic_user_id is None or category is None):
        raise DomainError("hồ sơ PO từ một hồ sơ sản phẩm cần PIC và nhóm sản phẩm")
    return POCase(
        id=id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        po_reference=po_reference,
        supplier_name=supplier_name,
        state=state,
        created_at=created_at,
        order_kind=order_kind,
        product_dev_case_id=product_dev_case_id,
        pic_user_id=pic_user_id,
        category=category,
    )


__all__ = [
    "IMPORTABLE_PO_STATES",
    "IMPORT_ACTION",
    "IMPORT_REASON",
    "check_dates",
    "imported_po_case",
]
