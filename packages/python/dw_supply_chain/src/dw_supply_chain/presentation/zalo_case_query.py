"""Read-only questions about PO cases and product-development cases through a
linked chat — zalo-channel ticket 06 (Z6), product cases since stage-1 ticket
08.

A chat command the worker registers in its `ChannelCommandRegistry`
(`dw_connectors.inbound`) after the decide command and the proposal
conversation; it satisfies that registry's `ChannelCommand` structurally. The
router has resolved the chat to a linked person, claimed the message once, and
built the context from that person's own membership in their chosen workspace,
cut to `QA_CEILING` — the two read scopes and nothing else, no role (ADR
0012 condition 2). Nothing below reads a person, tenant, workspace or scope from the
message.

There is no second answerer: the question goes to `AnswerCaseQuery`, the
handler behind the web's `POST /case-query`, with the same request rules
(`CaseQueryRequest`: length, no `<input>` tag) checked first. The model reads
the question into a typed intent, code grounds and resolves it under the
caller's own authorization and RLS — "an answer is never broader than the
question". What this module adds is only the reply, built by code from the
`CaseQueryAnswer` (the model writes no sentence of it):

* at most `MAX_LISTED` cases, each its PO number, supplier, state label and an
  absolute link (a product case: its proposal code, product name, state label
  and link); more than that says so and links the portal list with the same
  filter;
* the question's own words each filter came from, and what was left out
  (`ignored`, `unusable`);
* identifiers and states only — no amount, document or free-text note leaves
  through the chat (QE-20, provisional; E15): each line is built from the
  chat's view of the case (`zalo_views`), whose field list a test holds
  disjoint from every price field;
* a PO the asker cannot see — another tenant's, another workspace's — reads
  exactly as one that does not exist: the lookup never returns it.

When the question opens ONE case and the command has the case assistant
(ticket ai-automation/19), the reply also answers the question from that
case's own records, sentence by sentence with what each cites, or says there
is not enough evidence. It runs as a chat: under the chat's ceiling (no
document read) and with no price, whatever the asker holds on the portal.

A request to change something is not a question; the reading comes back
unsupported and the reply says the chat only asks, with the portal's link.
A product case the asker cannot see — another tenant's, another
workspace's — reads exactly as one that does not exist, as a PO does.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from pydantic import ValidationError

from dw_agent_runtime.model.budget import BudgetExceededError
from dw_kernel.errors import InfrastructureError, PermissionDeniedError, QuotaExceededError
from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.case_assistant import AnswerChannel, AskAboutCase, CitedAnswer
from dw_supply_chain.application.case_query import AnswerCaseQuery, CaseQueryAnswer
from dw_supply_chain.application.handlers import PO_CASE_READ, PRODUCT_CASE_READ
from dw_supply_chain.domain.case_document import CaseKind
from dw_supply_chain.domain.case_query import CaseQueryOutcome, GroundedField
from dw_supply_chain.domain.po_case import CaseState, POCase
from dw_supply_chain.domain.product_development_case import (
    ProductDevelopmentCase,
    ProductDevState,
)
from dw_supply_chain.presentation.routes import CaseQueryRequest
from dw_supply_chain.presentation.zalo_proposal import (
    BUDGET_SPENT,
    MODEL_CALL_TIMEOUT_SECONDS,
    NOT_UNDERSTOOD,
    quota_spent,
)
from dw_supply_chain.presentation.zalo_views import po_case_view, product_case_view

Reply = Callable[[str], Awaitable[None]]

# The two read scopes and nothing else: no write, no duty, no approvals.decide.
# What `AnswerCaseQuery` requires: `ListPOCases` for a PO question, and
# `ListProductCases` (and the Category list) for a product-case one.
QA_CEILING = frozenset({PO_CASE_READ, PRODUCT_CASE_READ})
ASSISTANT_UNAVAILABLE = "Chưa trả lời được nội dung câu hỏi lúc này; xem trên cổng."

# How many cases one reply names (ticket 06 step 4); the rest is a link.
MAX_LISTED = 10

NO_READ = "Anh/chị chưa có quyền xem hồ sơ cung ứng."
QUESTION_REFUSED = (
    "Câu hỏi quá dài hoặc có ký tự không dùng được; anh/chị hỏi ngắn gọn lại giúp mình."
)

_PO_CASES_PATH = "/supply-chain/po-cases"
_PRODUCT_CASES_PATH = "/supply-chain/product-cases"

# The glossary's words for each state (CONTEXT.md "Trạng thái của Hồ sơ PO",
# itself checked against the web's `CASE_STATE_LABEL`); a unit test fails when
# they disagree.
CASE_STATE_LABELS: Mapping[CaseState, str] = {
    CaseState.ORDER_REQUESTED: "Chờ tạo PO",
    CaseState.PO_CREATED: "Đã tạo PO",
    CaseState.WAITING_DEPOSIT: "Chờ đặt cọc",
    CaseState.DEPOSIT_CONFIRMED: "Đã xác nhận cọc",
    CaseState.PRE_PRODUCTION: "Chuẩn bị sản xuất",
    CaseState.PRODUCTION: "Đang sản xuất",
    CaseState.QC: "Kiểm tra chất lượng",
    CaseState.IN_TRANSIT: "Đang vận chuyển",
    CaseState.ARRIVED_PORT: "Đã đến cảng",
    CaseState.WAITING_PAYMENT: "Chờ thanh toán",
    CaseState.PAYMENT_COMPLETED: "Đã thanh toán",
    CaseState.WAREHOUSE_RECEIVING: "Đang nhập kho",
    CaseState.COMPLETED: "Hoàn tất",
    CaseState.WAITING_EXTERNAL: "Chờ bên ngoài",
    CaseState.BLOCKED: "Đang bị chặn",
    CaseState.REWORK: "Làm lại",
    CaseState.MANUAL_REVIEW: "Cần xem xét thủ công",
    CaseState.CANCELLED: "Đã hủy",
}

# The glossary's words for each product state (CONTEXT.md "Trạng thái của Hồ
# sơ phát triển sản phẩm", itself the web's `PRODUCT_DEV_STATE_LABEL`); a unit
# test fails when they disagree.
PRODUCT_STATE_LABELS: Mapping[ProductDevState, str] = {
    ProductDevState.PROPOSED: "Đề xuất",
    ProductDevState.SAMPLE_REQUESTED: "Đang lấy mẫu",
    ProductDevState.SAMPLE_TESTING: "Đang test mẫu",
    ProductDevState.REVISION_REQUESTED: "Chờ mẫu chỉnh sửa",
    ProductDevState.PENDING_BOD_REVIEW: "Chờ BGĐ duyệt",
    ProductDevState.PROFILE_IN_PROGRESS: "Đang làm BM04",
    ProductDevState.SUPPLIER_CONFIRMATION: "Chờ thống nhất với NCC",
    ProductDevState.ITEM_CODING: "Đang tạo mã hàng",
    ProductDevState.PENDING_SIGNOFF: "Chờ trình ký",
    ProductDevState.READY_TO_ORDER: "Sẵn sàng đặt hàng",
    ProductDevState.ORDERED: "Đã đặt hàng",
    ProductDevState.WAITING_EXTERNAL: "Chờ bên ngoài",
    ProductDevState.BLOCKED: "Đang bị chặn",
    ProductDevState.MANUAL_REVIEW: "Cần xem xét thủ công",
    ProductDevState.CANCELLED: "Đã hủy",
}

_FIELD_LABELS: Mapping[GroundedField, str] = {
    GroundedField.SUPPLIER: "nhà cung cấp",
    GroundedField.PO_REFERENCE: "mã PO",
    GroundedField.STATE: "trạng thái",
    GroundedField.ACTIVE_ONLY: "điều kiện “còn đang chạy”",
    GroundedField.PROPOSAL_CODE: "mã đề xuất",
    GroundedField.PRODUCT_STATE: "trạng thái hồ sơ phát triển",
    GroundedField.CATEGORY: "Category",
    GroundedField.PIC: "người phụ trách",
    GroundedField.MINE: "điều kiện “của tôi”",
}


class ChatMessage(Protocol):
    """What this command reads of `dw_connectors.inbound.InboundMessage`."""

    @property
    def channel(self) -> str: ...

    @property
    def text(self) -> str: ...


# ---- the reply, decided by code ---------------------------------------------------


def _base(web_url: str) -> str:
    return web_url.rstrip("/")


def _case_line(case: POCase, web_url: str) -> str:
    # Through the chat's view only (`zalo_views`): a reply cannot name what
    # the view does not carry.
    view = po_case_view(case)
    reference = view.po_reference or "(chưa có số PO)"
    return (
        f"- {reference} — {view.supplier_name}: {CASE_STATE_LABELS[view.state]} "
        f"{_base(web_url)}{_PO_CASES_PATH}/{view.id}"
    )


def _product_line(case: ProductDevelopmentCase, web_url: str) -> str:
    view = product_case_view(case)
    return (
        f"- {view.proposal_code} — {view.product_name}: {PRODUCT_STATE_LABELS[view.state]} "
        f"{_base(web_url)}{_PRODUCT_CASES_PATH}/{view.id}"
    )


def _product_list_url(answer: CaseQueryAnswer, web_url: str) -> str:
    """The portal's product case list narrowed exactly as the answer was
    (`productCasesHref`)."""
    plan = answer.plan
    params: dict[str, str] = {}
    if plan.product_state is not None:
        params["state"] = plan.product_state.value
    if plan.pic_user_id is not None:
        params["pic"] = str(plan.pic_user_id)
    if plan.category is not None:
        params["category"] = plan.category
    query = urlencode(params)
    return f"{_base(web_url)}{_PRODUCT_CASES_PATH}" + (f"?{query}" if query else "")


def _list_url(answer: CaseQueryAnswer, web_url: str) -> str:
    """The portal list narrowed exactly as the answer was (`poCasesHref`)."""
    plan = answer.plan
    params: dict[str, str] = {}
    if plan.state is not None:
        params["state"] = plan.state.value
    if plan.supplier_name is not None:
        params["supplier_name"] = plan.supplier_name
    if plan.active_only:
        params["active_only"] = "true"
    query = urlencode(params)
    return f"{_base(web_url)}{_PO_CASES_PATH}" + (f"?{query}" if query else "")


def read_only_hint(web_url: str) -> str:
    return (
        "Qua Zalo chỉ hỏi được về PO và hồ sơ phát triển sản phẩm; thao tác trên cổng: "
        f"{_base(web_url)}{_PO_CASES_PATH} hoặc {_base(web_url)}{_PRODUCT_CASES_PATH}"
    )


def _labels(fields: Sequence[GroundedField]) -> str:
    return ", ".join(_FIELD_LABELS[field] for field in fields)


def _quoted(answer: CaseQueryAnswer, field: GroundedField) -> str | None:
    return next((quote for named, quote in answer.citations if named is field), None)


def _not_understood(answer: CaseQueryAnswer, web_url: str) -> str:
    lines = [NOT_UNDERSTOOD]
    if answer.ignored:
        lines.append(f"Mình không thấy {_labels(answer.ignored)} trong câu hỏi, nên không dùng.")
    if answer.unusable:
        lines.append(
            f"Câu hỏi nêu {_labels(answer.unusable)} nhưng kiểu câu hỏi này không lọc theo đó được."
        )
    lines.append(read_only_hint(web_url))
    return "\n".join(lines)


def answer_text(answer: CaseQueryAnswer, web_url: str) -> str:
    """The chat reply for one answered question: identifiers, states and links
    of what the asker's own lookup returned, and the question's own words —
    nothing a model wrote, nothing the lookup did not return."""
    plan = answer.plan
    outcome = plan.outcome
    if outcome is CaseQueryOutcome.NOT_UNDERSTOOD:
        return _not_understood(answer, web_url)
    if outcome is CaseQueryOutcome.SUPPLIER_NOT_FOUND:
        return f"Không tìm thấy nhà cung cấp «{_quoted(answer, GroundedField.SUPPLIER)}»."
    if outcome is CaseQueryOutcome.SUPPLIER_AMBIGUOUS:
        return (
            f"«{_quoted(answer, GroundedField.SUPPLIER)}» khớp nhiều nhà cung cấp: "
            f"{', '.join(plan.candidates[:MAX_LISTED])}. Anh/chị nói rõ tên giúp mình."
        )
    if outcome is CaseQueryOutcome.PO_REFERENCE_MISSING:
        return "Anh/chị cho mình mã PO cần xem (ví dụ «PO-123 đang ở đâu»)."
    if outcome is CaseQueryOutcome.PO_NOT_FOUND:
        # The same sentence whether the PO does not exist or exists where the
        # asker cannot see it: the lookup ran under their context and RLS.
        return f"Không tìm thấy PO «{plan.po_reference}»."
    if outcome in _PRODUCT_OUTCOMES:
        return _product_answer(answer, web_url)

    lines: list[str] = []
    if answer.citations:
        lines.append("Hiểu từ câu hỏi: " + ", ".join(f"«{quote}»" for _, quote in answer.citations))
    if outcome is CaseQueryOutcome.OPEN:
        assert answer.opened is not None  # OPEN always carries the case
        lines.append(_case_line(answer.opened, web_url))
        return "\n".join(lines)
    if outcome is CaseQueryOutcome.PO_AMBIGUOUS:
        lines.append(f"Mã «{plan.po_reference}» khớp nhiều PO:")
    elif not answer.cases:
        lines.append("Không có PO nào khớp.")
        return "\n".join(lines)
    listed = answer.cases[:MAX_LISTED]
    lines.extend(_case_line(case, web_url) for case in listed)
    if answer.has_more or len(answer.cases) > len(listed):
        lines.append(f"Còn nữa, xem đủ ở: {_list_url(answer, web_url)}")
    return "\n".join(lines)


_PRODUCT_OUTCOMES = frozenset(
    {
        CaseQueryOutcome.PRODUCT_LIST,
        CaseQueryOutcome.PRODUCT_OPEN,
        CaseQueryOutcome.PROPOSAL_CODE_MISSING,
        CaseQueryOutcome.CATEGORY_NOT_FOUND,
        CaseQueryOutcome.CATEGORY_AMBIGUOUS,
        CaseQueryOutcome.PIC_NOT_FOUND,
        CaseQueryOutcome.PIC_AMBIGUOUS,
        CaseQueryOutcome.PRODUCT_NOT_FOUND,
        CaseQueryOutcome.PRODUCT_AMBIGUOUS,
    }
)


def _product_answer(answer: CaseQueryAnswer, web_url: str) -> str:
    """The reply about product-development cases, in the same shape as a PO
    reply: refusals name the question's own words, lists name at most
    `MAX_LISTED` cases and link the rest."""
    plan = answer.plan
    outcome = plan.outcome
    if outcome is CaseQueryOutcome.PROPOSAL_CODE_MISSING:
        return "Anh/chị cho mình mã đề xuất của hồ sơ cần xem (ví dụ «hồ sơ SP-028 tới đâu rồi»)."
    if outcome is CaseQueryOutcome.CATEGORY_NOT_FOUND:
        return f"Không có Category «{_quoted(answer, GroundedField.CATEGORY)}» trong danh sách."
    if outcome is CaseQueryOutcome.CATEGORY_AMBIGUOUS:
        return (
            f"«{_quoted(answer, GroundedField.CATEGORY)}» khớp nhiều Category: "
            f"{', '.join(plan.candidates[:MAX_LISTED])}. Anh/chị nói rõ giúp mình."
        )
    if outcome is CaseQueryOutcome.PIC_NOT_FOUND:
        return f"Không tìm thấy người «{_quoted(answer, GroundedField.PIC)}» trong workspace."
    if outcome is CaseQueryOutcome.PIC_AMBIGUOUS:
        return (
            f"«{_quoted(answer, GroundedField.PIC)}» khớp nhiều người: "
            f"{', '.join(plan.candidates[:MAX_LISTED])}. Anh/chị nói rõ tên giúp mình."
        )
    if outcome is CaseQueryOutcome.PRODUCT_NOT_FOUND:
        # Not found and not visible to the asker read the same (RLS).
        return f"Không tìm thấy hồ sơ phát triển «{plan.proposal_code}»."

    lines: list[str] = []
    if answer.citations:
        lines.append("Hiểu từ câu hỏi: " + ", ".join(f"«{quote}»" for _, quote in answer.citations))
    if outcome is CaseQueryOutcome.PRODUCT_OPEN:
        assert answer.opened_product is not None  # PRODUCT_OPEN always carries the case
        lines.append(_product_line(answer.opened_product, web_url))
        return "\n".join(lines)
    if outcome is CaseQueryOutcome.PRODUCT_AMBIGUOUS:
        lines.append(f"Mã «{plan.proposal_code}» khớp nhiều hồ sơ:")
    elif not answer.product_cases:
        lines.append("Không có hồ sơ phát triển nào khớp.")
        return "\n".join(lines)
    listed = answer.product_cases[:MAX_LISTED]
    lines.extend(_product_line(case, web_url) for case in listed)
    if answer.has_more or len(answer.product_cases) > len(listed):
        lines.append(f"Còn nữa, xem đủ ở: {_product_list_url(answer, web_url)}")
    return "\n".join(lines)


ASSISTANT_HEADER = "Trả lời từ hồ sơ (AI viết, đã kiểm dẫn chứng):"


def assistant_text(answer: CitedAnswer) -> str:
    """The assistant's answer in a chat: each kept sentence with the labels of
    what it cites, or the sentence saying there is not enough evidence."""
    if not answer.answered:
        return answer.text
    lines = [ASSISTANT_HEADER]
    for sentence in answer.sentences:
        sources = ", ".join(dict.fromkeys(answer.sources.get(k, k) for k in sentence.cites))
        lines.append(f"- {sentence.text} (nguồn: {sources})")
    return "\n".join(lines)


def _opened(answer: CaseQueryAnswer) -> tuple[CaseKind, uuid.UUID] | None:
    if answer.plan.outcome is CaseQueryOutcome.OPEN and answer.opened is not None:
        return CaseKind.PO, answer.opened.id.value
    if answer.plan.outcome is CaseQueryOutcome.PRODUCT_OPEN and answer.opened_product is not None:
        return CaseKind.PRODUCT, answer.opened_product.id.value
    return None


# ---- the command ------------------------------------------------------------------


@dataclass(frozen=True)
class ZaloCaseQueryCommand:
    # The handler behind `POST /case-query`, over the process's one-call
    # gateway: the plan's daily allowance is checked before the model call.
    answer: AnswerCaseQuery
    # The web app's public URL; every case named is linked to its page.
    web_url: str
    model_timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS
    # The case assistant (ticket ai-automation/19): answers the question about
    # the one case it opened. None: the reply names the case only.
    assistant: AskAboutCase | None = None

    @property
    def ceiling(self) -> frozenset[str]:
        return QA_CEILING

    async def handle(self, message: ChatMessage, context: AccessContext, reply: Reply) -> bool:
        """Always this command's message: the last in the walk, so whatever
        reaches it is answered, refused, or told what the chat can do."""
        try:
            # The web route's own rules for a question, not a copy of them.
            CaseQueryRequest(question=message.text)
        except ValidationError:
            await reply(QUESTION_REFUSED)
            return True
        try:
            answer = await asyncio.wait_for(
                self.answer.handle(context, message.text, channel=message.channel),
                timeout=self.model_timeout_seconds,
            )
        except PermissionDeniedError:
            await reply(NO_READ)
            return True
        except QuotaExceededError as exc:
            await reply(quota_spent(exc.message))
            return True
        except BudgetExceededError:
            await reply(BUDGET_SPENT)
            return True
        except (InfrastructureError, TimeoutError):
            await reply(f"{NOT_UNDERSTOOD}\n{read_only_hint(self.web_url)}")
            return True
        text = answer_text(answer, self.web_url)
        opened = _opened(answer)
        if self.assistant is not None and opened is not None:
            text = f"{text}\n{await self._assist(context, opened, message.text)}"
        await reply(text)
        return True

    async def _assist(
        self, context: AccessContext, opened: tuple[CaseKind, uuid.UUID], question: str
    ) -> str:
        assert self.assistant is not None
        kind, case_id = opened
        try:
            answer = await asyncio.wait_for(
                self.assistant.handle(
                    context,
                    case_kind=kind,
                    case_id=case_id,
                    question=question,
                    channel=AnswerChannel.ZALO,
                ),
                timeout=self.model_timeout_seconds,
            )
        except (
            PermissionDeniedError,
            QuotaExceededError,
            BudgetExceededError,
            InfrastructureError,
            TimeoutError,
        ):
            return ASSISTANT_UNAVAILABLE
        return assistant_text(answer)
