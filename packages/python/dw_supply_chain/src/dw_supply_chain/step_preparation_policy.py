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

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.case_document import CaseKind, DocumentType
from dw_supply_chain.domain.document_draft import DRAFT_TEMPLATES
from dw_supply_chain.domain.product_development_case import (
    ACTION_DOCUMENT_TYPE,
    DOCUMENT_REQUIRED_ACTIONS,
    ProductAction,
    ProductDevState,
    forward_step,
)
from dw_supply_chain.domain.step_proposal import PREPARABLE_ACTIONS, DraftRecipe, StepCheck

__all__ = [
    "STEP_PREPARATION_POLICY_ID",
    "PreparedDraft",
    "PreparedStep",
    "SupplyChainStepPreparation",
    "load_supply_chain_step_preparation",
]

STEP_PREPARATION_POLICY_ID = "supply_chain_step_preparation"


class PreparedDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_type: DocumentType
    recipe: DraftRecipe


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

    @property
    def action_document(self) -> DocumentType | None:
        return ACTION_DOCUMENT_TYPE.get(self.action)

    @property
    def drafted_types(self) -> frozenset[DocumentType]:
        return frozenset(d.doc_type for d in self.drafts)

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
        return self


class SupplyChainStepPreparation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    steps: tuple[PreparedStep, ...] = Field(default=(), max_length=40)

    @model_validator(mode="after")
    def _each_state_once(self) -> SupplyChainStepPreparation:
        keys = [(s.case_kind, s.state) for s in self.steps]
        if len(set(keys)) != len(keys):
            raise ValueError("a (case kind, state) is prepared twice")
        return self

    def step_for(self, case_kind: CaseKind, state: ProductDevState) -> PreparedStep | None:
        return next((s for s in self.steps if s.case_kind is case_kind and s.state is state), None)


def load_supply_chain_step_preparation(path: Path) -> SupplyChainStepPreparation:
    return SupplyChainStepPreparation.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )
