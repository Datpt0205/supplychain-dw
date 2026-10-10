"""Step 12's colour, packaging and pre-production sub-flow on a PO case (slice PK).

The part of process.md's sub-diagram that Cung ứng and R&D take today, while
the case sits in `pre_production`:

    approve the colour sample (Cung ứng) → TP MKT is told
    → approve the packaging design (Cung ứng)
    → receive the pre-production sample (Cung ứng)
    → pass or fail the pre-production test (R&D, with its report)

Each step can only follow the one before it, as the diagram draws them; a
colour or a design can be sent back for revision as often as needed until it
is approved, and an approval is final. A failed test can be taken again (a new
report); a passed one is final. MKT and Thiết kế are not users yet (Part B):
"đã báo TP MKT" is a line in this case's history, and the PIC is told.

MKT as a minimal user (ADR 0028, E17; ticket ai-automation/16): when the
tenant's packaging policy requires it (`require_packaging_content`), two steps
sit between the colour and the design: Cung ứng sends MKT its pack
(`send_mkt_pack`: BM04, HDSD, maquette; MKT is told instead of the "đã báo TP
MKT" line), and MKT submits the packaging content (`submit_packaging_content`,
with a `packaging_content` uploaded since the pack was sent, as often as it is
revised until the design is approved). The design is not approved before it.

One `PackagingDesign` per PO case. Every action needs the case in
`pre_production` (the repository re-checks that in the transaction that saves
the step, so a case that moved on meanwhile refuses it).

Whether the case may then go into `production` is `ProductionGate`'s answer:
when the tenant's packaging policy requires a passed pre-production test,
`POCase.start_production` refuses without one. The gate is required by the
method's signature, so no caller can start production without asking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from dw_kernel.errors import ConflictError, DomainError, DWError
from dw_kernel.ids import TenantId, WorkspaceId
from dw_supply_chain.domain.case_document import CaseDocument, CaseKind, DocumentType


class PackagingAction(StrEnum):
    APPROVE_COLOUR = "approve_colour"
    REQUEST_COLOUR_REVISION = "request_colour_revision"
    APPROVE_DESIGN = "approve_design"
    REQUEST_DESIGN_REVISION = "request_design_revision"
    RECEIVE_PRE_PRODUCTION_SAMPLE = "receive_pre_production_sample"
    PASS_PRE_PRODUCTION_TEST = "pass_pre_production_test"
    FAIL_PRE_PRODUCTION_TEST = "fail_pre_production_test"
    # MKT as a minimal user (ADR 0028; ticket ai-automation/16).
    SEND_MKT_PACK = "send_mkt_pack"
    SUBMIT_PACKAGING_CONTENT = "submit_packaging_content"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    REVISION_REQUESTED = "revision_requested"
    APPROVED = "approved"


class PreProductionTest(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


# Sending something back, or failing it, says why.
REASON_REQUIRED_ACTIONS = frozenset(
    {
        PackagingAction.REQUEST_COLOUR_REVISION,
        PackagingAction.REQUEST_DESIGN_REVISION,
        PackagingAction.FAIL_PRE_PRODUCTION_TEST,
    }
)

# The paper a step is taken on: the R&D test report, of this case, uploaded
# since the pre-production sample came in.
ACTION_DOCUMENT_TYPE: dict[PackagingAction, DocumentType] = {
    PackagingAction.PASS_PRE_PRODUCTION_TEST: DocumentType.PRE_PRODUCTION_TEST_REPORT,
    PackagingAction.FAIL_PRE_PRODUCTION_TEST: DocumentType.PRE_PRODUCTION_TEST_REPORT,
    # MKT's packaging content, uploaded since the pack was sent.
    PackagingAction.SUBMIT_PACKAGING_CONTENT: DocumentType.PACKAGING_CONTENT,
}
# The steps that exist only where the tenant's packaging policy requires the
# packaging content (`require_packaging_content`).
MKT_ACTIONS = frozenset({PackagingAction.SEND_MKT_PACK, PackagingAction.SUBMIT_PACKAGING_CONTENT})

# The history line approving the colour writes: MKT is not a user yet (Part B).
MKT_LEAD_NOTE = "Đã báo TP MKT: màu đạt, chuyển sang làm bao bì"
# The line sending MKT its pack writes, where MKT is a user (ticket ai-automation/16).
MKT_PACK_NOTE = "Đã gửi MKT gói BM04, HDSD, maquette"


@dataclass(frozen=True, slots=True)
class PackagingEvent:
    """One step taken, as its history row stores it."""

    action: PackagingAction
    reason: str | None
    document_id: uuid.UUID | None
    note: str | None


@dataclass(frozen=True, slots=True)
class PackagingHistoryEntry:
    """One already-persisted history row, read back for the case page."""

    action: PackagingAction
    reason: str | None
    document_id: uuid.UUID | None
    note: str | None
    actor_id: uuid.UUID
    occurred_at: datetime


def document_refusal(case_id: uuid.UUID, action: PackagingAction) -> DWError:
    """The one refusal for a paper a step cannot take: absent, foreign,
    unreadable to the caller, of the wrong type or older than the sample are
    one 409 naming the type that is missing, never which of those it was."""
    expected = ACTION_DOCUMENT_TYPE.get(action)
    if expected is None:
        return DomainError("this action takes no document", details={"action": action.value})
    since = (
        "từ khi gửi gói cho MKT"
        if action is PackagingAction.SUBMIT_PACKAGING_CONTENT
        else "từ khi nhận mẫu trước SX"
    )
    return ConflictError(
        f"{action.value} cần {expected.value} của hồ sơ này, tải lên {since}",
        details={
            "case_id": str(case_id),
            "action": action.value,
            "missing_document_type": expected.value,
        },
    )


@dataclass(slots=True)
class PackagingDesign:
    po_case_id: uuid.UUID
    tenant_id: TenantId
    workspace_id: WorkspaceId
    colour_status: ReviewStatus = ReviewStatus.PENDING
    design_status: ReviewStatus = ReviewStatus.PENDING
    pre_production_sample_received_at: datetime | None = None
    pre_production_test: PreProductionTest = PreProductionTest.PENDING
    # MKT's two steps (ticket ai-automation/16), where the tenant requires them.
    mkt_pack_sent_at: datetime | None = None
    packaging_content_submitted_at: datetime | None = None
    # 0 until the first step is saved: the row does not exist yet.
    version: int = 0
    _pending: list[PackagingEvent] = field(default_factory=list, compare=False, repr=False)

    def pop_pending_events(self) -> list[PackagingEvent]:
        events, self._pending = self._pending, []
        return events

    def take(
        self,
        action: PackagingAction,
        *,
        case_in_pre_production: bool,
        reason: str | None,
        document: CaseDocument | None,
        now: datetime,
        mkt_required: bool,
    ) -> None:
        """Takes one step, or raises without changing anything. `mkt_required`
        is the tenant's packaging policy: whether MKT's two steps stand between
        the colour and the design."""
        if not case_in_pre_production:
            raise ConflictError(
                "thiết kế màu, bao bì và test trước SX chỉ làm khi Hồ sơ PO ở bước 12",
                details={"case_id": str(self.po_case_id), "action": action.value},
            )
        reason = reason.strip() if reason else None
        if action in REASON_REQUIRED_ACTIONS and not reason:
            raise DomainError("this action requires a reason", details={"action": action.value})
        if action not in REASON_REQUIRED_ACTIONS:
            reason = None
        self._expect(action, self._open(action, mkt_required=mkt_required))
        document_id = self._step_document(action, document)
        note = self._apply(action, now, mkt_required=mkt_required)
        self.version += 1
        self._pending.append(PackagingEvent(action, reason, document_id, note))

    def _open(self, action: PackagingAction, *, mkt_required: bool) -> bool:
        """Whether `action` may follow what has been done, in the diagram's order."""
        match action:
            case PackagingAction.APPROVE_COLOUR | PackagingAction.REQUEST_COLOUR_REVISION:
                return self.colour_status is not ReviewStatus.APPROVED
            case PackagingAction.SEND_MKT_PACK:
                return (
                    mkt_required
                    and self.colour_status is ReviewStatus.APPROVED
                    and self.mkt_pack_sent_at is None
                )
            case PackagingAction.SUBMIT_PACKAGING_CONTENT:
                return (
                    mkt_required
                    and self.mkt_pack_sent_at is not None
                    and self.design_status is not ReviewStatus.APPROVED
                )
            case PackagingAction.APPROVE_DESIGN | PackagingAction.REQUEST_DESIGN_REVISION:
                return self._design_open() and (
                    not mkt_required or self.packaging_content_submitted_at is not None
                )
            case PackagingAction.RECEIVE_PRE_PRODUCTION_SAMPLE:
                return (
                    self.design_status is ReviewStatus.APPROVED
                    and self.pre_production_sample_received_at is None
                )
            case (
                PackagingAction.PASS_PRE_PRODUCTION_TEST | PackagingAction.FAIL_PRE_PRODUCTION_TEST
            ):
                return self._test_open()

    def _apply(self, action: PackagingAction, now: datetime, *, mkt_required: bool) -> str | None:
        match action:
            case PackagingAction.APPROVE_COLOUR:
                self.colour_status = ReviewStatus.APPROVED
                # Where MKT is a user, MKT is told when the pack is sent.
                return None if mkt_required else MKT_LEAD_NOTE
            case PackagingAction.SEND_MKT_PACK:
                self.mkt_pack_sent_at = now
                return MKT_PACK_NOTE
            case PackagingAction.SUBMIT_PACKAGING_CONTENT:
                self.packaging_content_submitted_at = now
            case PackagingAction.REQUEST_COLOUR_REVISION:
                self.colour_status = ReviewStatus.REVISION_REQUESTED
            case PackagingAction.APPROVE_DESIGN:
                self.design_status = ReviewStatus.APPROVED
            case PackagingAction.REQUEST_DESIGN_REVISION:
                self.design_status = ReviewStatus.REVISION_REQUESTED
            case PackagingAction.RECEIVE_PRE_PRODUCTION_SAMPLE:
                self.pre_production_sample_received_at = now
            case PackagingAction.PASS_PRE_PRODUCTION_TEST:
                self.pre_production_test = PreProductionTest.PASSED
            case PackagingAction.FAIL_PRE_PRODUCTION_TEST:
                self.pre_production_test = PreProductionTest.FAILED
        return None

    def _design_open(self) -> bool:
        return (
            self.colour_status is ReviewStatus.APPROVED
            and self.design_status is not ReviewStatus.APPROVED
        )

    @property
    def test_open(self) -> bool:
        """The pre-production test may be taken (and measured for): the
        sample is in and the test is not passed."""
        return self._test_open()

    def _test_open(self) -> bool:
        return (
            self.pre_production_sample_received_at is not None
            and self.pre_production_test is not PreProductionTest.PASSED
        )

    def _expect(self, action: PackagingAction, allowed: bool) -> None:
        if not allowed:
            raise ConflictError(
                f"không thể {action.value} ở bước con hiện tại",
                details={
                    "case_id": str(self.po_case_id),
                    "action": action.value,
                    "colour_status": self.colour_status.value,
                    "design_status": self.design_status.value,
                    "pre_production_test": self.pre_production_test.value,
                },
            )

    def _step_document(
        self, action: PackagingAction, document: CaseDocument | None
    ) -> uuid.UUID | None:
        expected = ACTION_DOCUMENT_TYPE.get(action)
        if expected is None:
            if document is not None:
                raise document_refusal(self.po_case_id, action)
            return None
        since = (
            self.mkt_pack_sent_at
            if action is PackagingAction.SUBMIT_PACKAGING_CONTENT
            else self.pre_production_sample_received_at
        )
        belongs = (
            document is not None
            and document.case_kind is CaseKind.PO
            and document.case_id == self.po_case_id
            and document.tenant_id == self.tenant_id.value
            and document.workspace_id == self.workspace_id.value
            and document.doc_type is expected
            and since is not None
            and document.uploaded_at >= since
        )
        if not belongs:
            raise document_refusal(self.po_case_id, action)
        assert document is not None
        return document.id.value


@dataclass(frozen=True, slots=True)
class ProductionGate:
    """Whether a PO case may go from `pre_production` into `production`.

    `required` is the tenant's packaging policy; `test` the case's
    pre-production test result (`pending` when nothing was recorded)."""

    required: bool
    test: PreProductionTest

    def check(self, case_id: uuid.UUID) -> None:
        if self.required and self.test is not PreProductionTest.PASSED:
            raise ConflictError(
                "chưa vào sản xuất được: R&D chưa đạt test trước sản xuất",
                details={
                    "case_id": str(case_id),
                    "reason": "pre_production_test_not_passed",
                    "pre_production_test": self.test.value,
                },
            )
