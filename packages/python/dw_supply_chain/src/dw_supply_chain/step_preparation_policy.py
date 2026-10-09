"""Which steps AI prepares, per tenant (ADR 0025 point 9; ticket
ai-automation/05).

Keyed by (case kind, state): when a case enters that state, the lane
`supply_chain_step_preparation` starts one preparation run, which reads the
step's `sources`, fills its `drafts` (each by a `DraftRecipe`), runs its
`checks` and raises one approval to take `action`. A `physical` step (a test, a
count) gets a record with an empty result a person types on the web, beside
AI's suggestion; its `result_fields` are fields of the drafted document of
`action`.

The platform default (`configs/policies/supply_chain_step_preparation@1.0.0.yaml`)
lists nothing: no tenant gets a run until it opts in. A tenant replaces it
whole through `PolicyOverridePort` (`PUT /supply-chain/step-preparation-policy`,
`supply_chain.action_duties.write`), as the packaging policy. Placed at the
package's top level, like `product_action_duties.py`: a versioned artifact's
schema and parser, touching the filesystem.

Everything a step names is checked when the document loads, so an entry that
would do nothing (a step no proposal can carry, a step whose paper nobody
prepares, a recipe or check nobody implements) is refused by name rather than
read by nothing (failure-modes #1). PO cases (steps 10-17) come with their own
tickets (AI-14 to AI-18); 1.0.0 refuses them rather than accept and ignore them.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_kernel.errors import DomainError
from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DRAFT_TEMPLATES
from dw_supply_chain.domain.product_development_case import (
    ACTION_DOCUMENT_TYPE,
    DOCUMENT_REQUIRED_ACTIONS,
    ProductAction,
    ProductDevState,
    forward_step,
)
from dw_supply_chain.domain.step_proposal import (
    OUTCOME_REASON_ACTIONS,
    PREPARABLE_ACTIONS,
    DraftRecipe,
    StepCheck,
)
from dw_supply_chain.domain.supplier_message import MessagePurpose

__all__ = [
    "STEP_PREPARATION_POLICY_ID",
    "PreparedDraft",
    "PreparedStep",
    "StepOutcome",
    "SupplyChainStepPreparation",
    "load_supply_chain_step_preparation",
]

STEP_PREPARATION_POLICY_ID = "supply_chain_step_preparation"


class PreparedDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_type: DocumentType
    recipe: DraftRecipe


class StepOutcome(BaseModel):
    """One outcome a person may choose for a physical step: the step it
    takes and the words the record prints for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ProductAction
    label: str = Field(min_length=1, max_length=60)


class PreparedStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_kind: CaseKind
    state: ProductDevState
    action: ProductAction
    sources: tuple[DocumentType, ...] = Field(default=(), max_length=10)
    drafts: tuple[PreparedDraft, ...] = Field(default=(), max_length=5)
    checks: tuple[StepCheck, ...] = ()
    physical: bool = False
    result_fields: tuple[str, ...] = Field(default=(), max_length=10)
    # A physical step with more than one outcome (ticket ai-automation/09):
    # the result field the person chooses the outcome in, and each choice's
    # step. `action` is the outcome the proposal is raised as; the one chosen
    # is the one taken.
    outcome_field: str | None = None
    outcomes: dict[str, StepOutcome] = Field(default_factory=dict, max_length=5)

    @property
    def action_document(self) -> DocumentType | None:
        return ACTION_DOCUMENT_TYPE.get(self.action)

    @property
    def drafted_types(self) -> frozenset[DocumentType]:
        return frozenset(d.doc_type for d in self.drafts)

    @property
    def outcome_actions(self) -> frozenset[ProductAction]:
        """Every step this proposal may take: its outcomes', or its own."""
        return frozenset(o.action for o in self.outcomes.values()) or frozenset({self.action})

    def outcome_for(self, typed: Mapping[str, str]) -> ProductAction:
        """The step the typed result chooses; the step's own action when it
        has no outcomes. A choice it does not offer is refused by name."""
        if not self.outcomes or self.outcome_field is None:
            return self.action
        choice = typed.get(self.outcome_field)
        if choice not in self.outcomes:
            raise DomainError(
                "kết luận phải là một trong các lựa chọn của bước",
                details={"field": self.outcome_field, "choices": sorted(self.outcomes)},
            )
        return self.outcomes[choice].action

    @model_validator(mode="after")
    def _a_step_a_proposal_can_take(self) -> PreparedStep:
        name = f"{self.case_kind.value}/{self.state.value}"
        if self.case_kind is not CaseKind.PRODUCT:
            raise ValueError(f"{name}: PO steps are prepared from AI-14 on, not in 1.0.0")
        if self.action not in PREPARABLE_ACTIONS:
            raise ValueError(
                f"{name}: {self.action.value} needs more than a document; a proposal cannot take it"
            )
        step = forward_step(self.action)
        if step is None or step[0] is not self.state:
            raise ValueError(f"{name}: {self.action.value} is not taken from {self.state.value}")
        drafted = [d.doc_type for d in self.drafts]
        if len(set(drafted)) != len(drafted):
            raise ValueError(f"{name}: a document type is drafted twice")
        untemplated = sorted(t.value for t in drafted if t not in DRAFT_TEMPLATES)
        if untemplated:
            raise ValueError(f"{name}: no template drafts {untemplated}")
        paper = self.action_document
        if (
            self.action in DOCUMENT_REQUIRED_ACTIONS
            and paper is not None
            and paper not in self.drafted_types
            and paper not in self.sources
        ):
            raise ValueError(
                f"{name}: {self.action.value} needs a {paper.value}; draft it or name it a source"
            )
        if self.physical:
            if not self.result_fields:
                raise ValueError(f"{name}: a physical step names the result a person types")
            if paper is None or paper not in self.drafted_types:
                raise ValueError(f"{name}: the result is typed on the drafted paper of the step")
        elif self.result_fields:
            raise ValueError(f"{name}: only a physical step has result fields")
        if len(set(self.result_fields)) != len(self.result_fields):
            raise ValueError(f"{name}: a result field is named twice")
        if self.outcomes or self.outcome_field is not None:
            self._outcomes_a_proposal_can_take(name)
        return self

    def _outcomes_a_proposal_can_take(self, name: str) -> None:
        if not self.physical or self.outcome_field not in self.result_fields:
            raise ValueError(f"{name}: outcomes are chosen in a result field of a physical step")
        if not self.outcomes:
            raise ValueError(f"{name}: an outcome field needs outcomes")
        if self.action not in {o.action for o in self.outcomes.values()}:
            raise ValueError(f"{name}: {self.action.value} is not one of its outcomes")
        for choice, outcome in self.outcomes.items():
            action = outcome.action
            if action not in PREPARABLE_ACTIONS | OUTCOME_REASON_ACTIONS:
                raise ValueError(f"{name}/{choice}: a proposal cannot take {action.value}")
            step = forward_step(action)
            if step is None or step[0] is not self.state:
                raise ValueError(f"{name}/{choice}: {action.value} is not taken from here")
            paper = ACTION_DOCUMENT_TYPE.get(action)
            if (
                action in DOCUMENT_REQUIRED_ACTIONS
                and paper is not None
                and paper not in self.drafted_types
                and paper not in self.sources
            ):
                raise ValueError(f"{name}/{choice}: {action.value} needs a {paper.value}")


class SupplyChainStepPreparation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    steps: tuple[PreparedStep, ...] = Field(default=(), max_length=40)
    # The messages to a supplier AI drafts for this tenant (ADR 0029; ticket
    # ai-automation/07), worded by `supply_chain_supplier_messages`; a person
    # copies and sends each. Empty: none is drafted.
    supplier_messages: tuple[MessagePurpose, ...] = ()
    # The tờ trình BGĐ AI drafts before BGĐ's review is raised (ticket
    # ai-automation/10). False: the review is raised without one.
    bod_submission: bool = False

    @model_validator(mode="after")
    def _each_state_once(self) -> SupplyChainStepPreparation:
        keys = [(s.case_kind, s.state) for s in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError("a (case kind, state) is prepared twice")
        if len(set(self.supplier_messages)) != len(self.supplier_messages):
            raise ValueError("a supplier message purpose is listed twice")
        return self

    def step_for(self, case_kind: CaseKind, state: ProductDevState) -> PreparedStep | None:
        return next((s for s in self.steps if s.case_kind is case_kind and s.state is state), None)


def load_supply_chain_step_preparation(path: Path) -> SupplyChainStepPreparation:
    return SupplyChainStepPreparation.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
