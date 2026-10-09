---
status: Proposed (provisional, decided by the agent on Đạt's direction 2026-10-09; upstream candidate)
date: 2026-10-09
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/model/skills.py
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/model/prompts.py
    - ../../scripts/release_manifest.py
    - ../../.claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md
---

# 0030. Skills are versioned artifacts a prompt declares

Numbered 0030 so that it collides with no ADR here or in the supply-chain
package (those use numbers up to 0029). Generic: an upstream candidate for the
platform repository (ADR 0011).

## Context

Process knowledge reached a model only by being pasted into a prompt: the
seventeen steps of a process, what each document holds, the content a label
must carry, how a quality inspection is sampled. Two prompts needing the same
knowledge carried two copies of it (failure-modes #2), a customer whose process
differs had to fork the prompt, and a release could not say which version of
that knowledge a run read. The runtime had no notion of a skill; an old comment
about `configs/skills/sales_chat` was all that remained of one.

## Decision

1. **A skill is a versioned artifact**, `configs/skills/<domain>/<skill_id>@<version>.yaml`:
   `title`, `body`, `applies_to` (the prompt ids that may declare it). Immutable
   once released; a change is a new version. Loaded by `SkillRegistry`
   (`dw_agent_runtime.model.skills`).
2. **A prompt declares the skills it reads** (`skills: [id@1.2.0]` or
   `[id@^1.2.0]`: same major, this version or later, newest wins). The prompt
   registry appends each to the prompt's **system** part under a fixed heading:
   trusted, reviewed text, never an `<input>` variable (ADR 0010 is about data;
   a skill is not data). The rendering names the versions it used
   (`RenderedPrompt.skills`) and its checksum covers their words.
3. **Checked when loaded, by name:** a skill's `applies_to` must name prompts
   that exist; a prompt may declare only a skill that resolves and names it.
   `load_shipped_prompts(configs_dir)` is the one way a host builds its prompt
   registry (API, worker, evals), so the check cannot be skipped by one of them.
4. **Per tenant through `TenantOverlay`:** a tenant's own version of a skill if
   it has one satisfying the range, the platform's otherwise, never another
   tenant's (`TenantOverlay.tenant_values` reads one tenant's layer only).
5. **Pinned in the release manifest** (`skills`: id, version, `applies_to`,
   checksum; each prompt bundle lists the ranges it declares), so a run's
   release names the skill versions its prompts resolved on the platform layer.

## Consequences

- A skill no prompt declares yet (`applies_to: []`) loads and is pinned; it is
  read by nothing until a prompt declares it, which the ticket that adds that
  prompt does. Supply chain ships four such (label rules, AQL, customs file,
  supplier email style) for tickets ai-automation/07, 16 and 17.
- Loading a tenant's own skills from storage at run time is not built, as it is
  not for prompts: the registry takes them (`load_bytes(..., tenant_id=)`); the
  store is the same work for prompts and skills and lands with the first tenant
  that needs either.
- Changing a skill's words changes what a prompt renders without a new prompt
  version. That is the point (one copy of the knowledge), and it is why the
  rendering names the skill versions and the manifest pins them.
