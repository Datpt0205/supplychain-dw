"""One structured model call that writes cited sentences (tickets
ai-automation/07-10).

A plain function, not a graph, for the reason `workflows.brief_summary` gives:
one call with nothing to orchestrate. It takes `ModelGateway` by injection, so
it holds no provider SDK and no SQL. What the model sees is the task and the
evidence code assembled, as the prompt's one untrusted variable; its answer is
a claim, and `domain.grounded_writing.ground_sentences` decides what a person
reads.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from dw_agent_runtime.contracts import RunContext
from dw_agent_runtime.ports import ModelGateway, ModelRequest, OutputT
from dw_supply_chain.domain.grounded_writing import EvidenceItem, evidence_json

# The prompts' one untrusted variable.
EVIDENCE_VARIABLE = "evidence"


@dataclass(frozen=True, slots=True)
class WritingPrompt:
    prompt_id: str
    prompt_version: str

    @property
    def ref(self) -> tuple[str, str]:
        return self.prompt_id, self.prompt_version


async def write_with_evidence(
    gateway: ModelGateway,
    run_context: RunContext,
    prompt: WritingPrompt,
    output_type: type[OutputT],
    *,
    task: Mapping[str, object],
    evidence: Iterable[EvidenceItem],
    model_profile: str | None,
) -> OutputT:
    """The model's writing, schema-validated by the gateway; not yet checked
    against the evidence, which is the caller's job."""
    request = ModelRequest(
        task="reasoning",
        prompt_id=prompt.prompt_id,
        prompt_version=prompt.prompt_version,
        variables={EVIDENCE_VARIABLE: evidence_json(task, evidence)},
        model_profile=model_profile,
        route_kind="reasoning",
    )
    return await gateway.generate_structured(request, output_type, run_context=run_context)


__all__ = ["EVIDENCE_VARIABLE", "WritingPrompt", "write_with_evidence"]
