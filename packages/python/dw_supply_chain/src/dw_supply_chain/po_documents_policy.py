"""Which paper a PO case's step needs on the case before it is taken, per
tenant (ticket ai-automation/15; QE-02).

The platform default (`configs/policies/supply_chain_po_documents@1.0.0.yaml`)
requires none, so every tenant behaves as before; a tenant names its own
through the same `PolicyOverridePort` as its other policies (Elmich's:
`scripts/elmich_po_documents_override.yaml`: the deposit papers before the
deposit is confirmed, the payment papers before the final payment is). Read
where the step is taken (`application.po_papers.PaperGateResolver`), on every
path to it: a person's click, an approval of the tenant's matrix, and a step
proposal AI prepared.

Placed at the package's top level, like `packaging_policy.py`: a versioned
artifact's schema and parser, touching the filesystem.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dw_supply_chain.domain.case_document import DocumentType
from dw_supply_chain.domain.po_case import CaseAction

__all__ = [
    "PO_DOCUMENTS_POLICY_ID",
    "SupplyChainPODocuments",
    "load_supply_chain_po_documents",
]

PO_DOCUMENTS_POLICY_ID = "supply_chain_po_documents"


class SupplyChainPODocuments(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    policy_id: str = Field(pattern=f"^{PO_DOCUMENTS_POLICY_ID}$")
    policy_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    required: dict[CaseAction, DocumentType] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _a_step_a_person_takes(self) -> SupplyChainPODocuments:
        # create_po's paper is the PO itself, written in its own command; a
        # rule on it would be read by nothing (failure-modes #1).
        if CaseAction.CREATE_PO in self.required:
            raise ValueError("create_po makes its own paper; it takes no required document")
        return self

    def paper_for(self, action: CaseAction) -> DocumentType | None:
        return self.required.get(action)


def load_supply_chain_po_documents(path: Path) -> SupplyChainPODocuments:
    return SupplyChainPODocuments.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
