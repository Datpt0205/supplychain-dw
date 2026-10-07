"""The command bar: a question about cases in, a structured answer out.

Asked on the web (`POST /case-query`) and in a linked chat (Zalo, zalo-channel
ticket 06), through this one handler. Two kinds of case are answered: PO
cases and, since stage-1 ticket 08, product-development cases (Hồ sơ phát
triển sản phẩm).

Its own module rather than `handlers.py`: a product-case question runs the
product case list (`product_cases.ListProductCases`), and that module already
builds on `handlers.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelOutputInvalidError
from dw_kernel.ports import IdGenerator
from dw_platform.application.access_context import AccessContext
from dw_platform.application.ports import AuthorizationPort
from dw_supply_chain.application.handlers import (
    PO_CASE_READ,
    PRODUCT_CASE_READ,
    WORKER_VERSION,
    ListPOCases,
    ListProductCategories,
)
from dw_supply_chain.application.ports import (
    POCaseListFilter,
    POCaseRepositoryPort,
    ProductCaseListFilter,
    ProductCaseLookupPort,
    WorkspaceDirectoryPort,
)
from dw_supply_chain.application.product_cases import ListProductCases
from dw_supply_chain.domain.case_query import (
    PRODUCT_KINDS,
    CaseQueryKind,
    CaseQueryOutcome,
    CaseQueryPlan,
    GroundedField,
    ground,
    ignored_fields,
    plan_case_query,
)
from dw_supply_chain.domain.po_case import POCase
from dw_supply_chain.domain.product_development_case import ProductDevelopmentCase
from dw_supply_chain.sla_policy import ProductCategory
from dw_supply_chain.workflows.case_query_understanding import understand_case_query

# Not a registered worker (no configs/workers entry): one structured-extraction
# call, like the other Supply Chain model calls (`handlers.py`).
_CASE_QUERY_WORKER_ID = "supply_chain.case_query_understanding"

# How many rows a command-bar answer carries. The answer is a work surface,
# not the list itself: `has_more` offers the full, paged list instead.
_ANSWER_ROWS = 20


@dataclass(frozen=True, slots=True)
class CaseQueryAnswer:
    """What one question came to — every part of it decided by code.

    `plan.outcome` says which fields mean anything: `cases`/`has_more` for a
    LIST (and the matching cases for a PO_AMBIGUOUS), `opened` for an OPEN;
    `product_cases`/`has_more` for a PRODUCT_LIST (and the matches of a
    PRODUCT_AMBIGUOUS), `opened_product` for a PRODUCT_OPEN.
    `citations` are the question's own spans behind every field that was
    grounded, whatever the outcome. A refusal's two reasons are kept apart:
    `ignored` holds fields the model claimed that the question gave no
    grounds for; `unusable` holds fields the question did state but that
    this kind of answer cannot apply.
    """

    intent: CaseQueryKind
    plan: CaseQueryPlan
    citations: tuple[tuple[GroundedField, str], ...] = ()
    ignored: tuple[GroundedField, ...] = ()
    unusable: tuple[GroundedField, ...] = ()
    cases: tuple[POCase, ...] = ()
    has_more: bool = False
    opened: POCase | None = None
    product_cases: tuple[ProductDevelopmentCase, ...] = ()
    opened_product: ProductDevelopmentCase | None = None


@dataclass(frozen=True)
class AnswerCaseQuery:
    """The model interprets and code decides, end to end: it reads the
    question into a typed intent (`workflows.case_query_understanding`);
    `domain.case_query` grounds it in the question and plans against the
    caller's real data (supplier names, the tenant's Category list, the people
    of the workspace); the lookups run through the list handlers and the
    repositories, under the caller's own authorization and RLS. Nothing the
    model returned is used as an identifier until code has resolved it.

    Authorization comes first — before a token is spent: `PO_CASE_READ`, as
    before ticket 08. A reading about product cases then needs
    `PRODUCT_CASE_READ` too, checked before anything of them is read. A model
    whose every answer failed the schema degrades to NOT_UNDERSTOOD; any
    other failure (a budget refusal, an unavailable provider) is raised as
    what it is, never dressed up as a misunderstanding.
    """

    po_case_repo: POCaseRepositoryPort
    list_cases: ListPOCases
    product_cases: ProductCaseLookupPort
    list_product_cases: ListProductCases
    categories: ListProductCategories
    directory: WorkspaceDirectoryPort
    gateway: ModelGateway
    authz: AuthorizationPort
    ids: IdGenerator

    async def handle(
        self, context: AccessContext, question: str, *, channel: str = "web"
    ) -> CaseQueryAnswer:
        """`channel` is where the question came from (the command bar, a linked
        chat), carried into the run's trace; it decides nothing."""
        await self.authz.require(context=context, action=PO_CASE_READ, resource_type="po_case")

        run_id = self.ids.new_uuid()
        run_context = RunContext(
            run_id=run_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.principal_id,
            worker_id=_CASE_QUERY_WORKER_ID,
            worker_version=WORKER_VERSION,
            channel=channel,
            plan_id=context.plan_id,
            roles=context.roles,
            scopes=context.scopes,
            trace_id=str(run_id),
        )
        try:
            intent = await understand_case_query(self.gateway, run_context, question)
        except ModelOutputInvalidError:
            return CaseQueryAnswer(
                intent=CaseQueryKind.UNSUPPORTED,
                plan=CaseQueryPlan(outcome=CaseQueryOutcome.NOT_UNDERSTOOD),
            )

        grounded = ground(intent, question)
        if grounded.kind in PRODUCT_KINDS:
            await self.authz.require(
                context=context, action=PRODUCT_CASE_READ, resource_type="product_dev_case"
            )
        # The caller's own names, each read only for the reading that
        # resolves against them. The model never sees any of them.
        listing_po = grounded.kind is CaseQueryKind.LIST_CASES
        listing_products = grounded.kind is CaseQueryKind.LIST_PRODUCT_CASES
        known_suppliers = (
            await self.po_case_repo.list_supplier_names(context)
            if listing_po and grounded.supplier_mention is not None
            else []
        )
        categories: list[ProductCategory] = (
            await self.categories.handle(context)
            if listing_products and grounded.category_mention is not None
            else []
        )
        members = (
            [
                (member.user_id, member.display_name)
                for member in await self.directory.list_members(context)
            ]
            if listing_products and grounded.pic_mention is not None
            else []
        )
        plan = plan_case_query(
            grounded,
            known_suppliers,
            categories=categories,
            members=members,
            caller=context.principal_id,
        )
        answered = CaseQueryAnswer(
            intent=grounded.kind,
            plan=plan,
            citations=grounded.citations,
            ignored=ignored_fields(grounded),
            unusable=plan.unused,
        )

        if plan.outcome is CaseQueryOutcome.LIST:
            page = await self.list_cases.handle(
                context,
                POCaseListFilter(
                    state=plan.state,
                    supplier_name=plan.supplier_name,
                    active_only=plan.active_only,
                ),
                limit=_ANSWER_ROWS,
                cursor=None,
            )
            return replace(answered, cases=page.items, has_more=page.next_cursor is not None)

        if plan.outcome is CaseQueryOutcome.OPEN:
            assert plan.po_reference is not None  # OPEN always carries one
            matches = await self.po_case_repo.find_by_reference(context, plan.po_reference)
            if len(matches) == 1:
                # The stored reference, not the model's spelling of it.
                return replace(
                    answered,
                    plan=replace(plan, po_reference=matches[0].po_reference),
                    opened=matches[0],
                )
            return replace(
                answered,
                plan=replace(
                    plan,
                    outcome=(
                        CaseQueryOutcome.PO_AMBIGUOUS if matches else CaseQueryOutcome.PO_NOT_FOUND
                    ),
                    # `find_by_reference` never matches a case without one.
                    candidates=tuple(
                        case.po_reference for case in matches if case.po_reference is not None
                    ),
                ),
                cases=tuple(matches),
            )

        if plan.outcome is CaseQueryOutcome.PRODUCT_LIST:
            product_page = await self.list_product_cases.handle(
                context,
                ProductCaseListFilter(
                    state=plan.product_state,
                    pic_user_id=plan.pic_user_id,
                    category=plan.category,
                ),
                limit=_ANSWER_ROWS,
                cursor=None,
            )
            return replace(
                answered,
                product_cases=tuple(product_page.items),
                has_more=product_page.next_cursor is not None,
            )

        if plan.outcome is CaseQueryOutcome.PRODUCT_OPEN:
            assert plan.proposal_code is not None  # PRODUCT_OPEN always carries one
            found = await self.product_cases.find_by_proposal_code(context, plan.proposal_code)
            if len(found) == 1:
                return replace(
                    answered,
                    plan=replace(plan, proposal_code=found[0].proposal_code),
                    opened_product=found[0],
                )
            return replace(
                answered,
                plan=replace(
                    plan,
                    outcome=(
                        CaseQueryOutcome.PRODUCT_AMBIGUOUS
                        if found
                        else CaseQueryOutcome.PRODUCT_NOT_FOUND
                    ),
                    candidates=tuple(case.proposal_code for case in found),
                ),
                product_cases=tuple(found),
            )

        return answered
