"""The read-only case assistant on the portal (ticket ai-automation/19).

`POST /po-cases/{id}/questions` and `POST /product-cases/{id}/questions` — a
question about this case, answered from its own records with citations, or
"không đủ bằng chứng". Read-only: nothing is written, so no
`Idempotency-Key`. The request names the question only; never a tenant, a
scope, a channel or what may be cited.
"""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from dw_platform.application.access_context import AccessContext
from dw_supply_chain.application.case_assistant import AnswerChannel, AskAboutCase, CitedAnswer
from dw_supply_chain.domain.case_answer import MAX_QUESTION
from dw_supply_chain.domain.case_document import CaseKind

AccessContextResolver = Callable[..., object]


class CaseQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION, pattern=r"^[^\x00]*$")


class AnswerCitationView(BaseModel):
    key: str
    label: str


class AnswerSentenceView(BaseModel):
    text: str
    cites: list[AnswerCitationView]


class CaseAnswerView(BaseModel):
    # False: "không đủ bằng chứng" (`text` says so), no sentence.
    answered: bool
    text: str
    # AI-written, each kept only because it checks out against what it cites.
    sentences: list[AnswerSentenceView]
    # How many sentences the model wrote that did not check out.
    dropped: int


def answer_view(answer: CitedAnswer) -> CaseAnswerView:
    return CaseAnswerView(
        answered=answer.answered,
        text=answer.text,
        sentences=[
            AnswerSentenceView(
                text=s.text,
                cites=[AnswerCitationView(key=k, label=answer.sources.get(k, k)) for k in s.cites],
            )
            for s in answer.sentences
        ],
        dropped=answer.dropped,
    )


def build_case_assistant_router(
    ask: AskAboutCase, *, resolve_access_context: AccessContextResolver
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/supply-chain", tags=["supply_chain"])
    require_access_context = Annotated[AccessContext, Depends(resolve_access_context)]

    @router.post("/po-cases/{case_id}/questions", response_model=CaseAnswerView)
    async def ask_about_po_case(
        case_id: uuid.UUID, body: CaseQuestionRequest, context: require_access_context
    ) -> CaseAnswerView:
        return answer_view(
            await ask.handle(
                context,
                case_kind=CaseKind.PO,
                case_id=case_id,
                question=body.question,
                channel=AnswerChannel.WEB,
            )
        )

    @router.post("/product-cases/{case_id}/questions", response_model=CaseAnswerView)
    async def ask_about_product_case(
        case_id: uuid.UUID, body: CaseQuestionRequest, context: require_access_context
    ) -> CaseAnswerView:
        return answer_view(
            await ask.handle(
                context,
                case_kind=CaseKind.PRODUCT,
                case_id=case_id,
                question=body.question,
                channel=AnswerChannel.WEB,
            )
        )

    return router


__all__ = ["CaseAnswerView", "CaseQuestionRequest", "answer_view", "build_case_assistant_router"]
