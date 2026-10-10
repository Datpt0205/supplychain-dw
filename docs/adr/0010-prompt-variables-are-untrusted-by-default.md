---
status: Accepted (provisional, decided under delegation 2026-10-08)
date: 2026-10-08
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/model/prompts.py # contain_untrusted, raw_variables
    - ../../packages/python/dw_agent_runtime/tests/unit/test_prompt_containment.py
---

# 0010. Every prompt variable is untrusted unless its template says why not

## Context

`PromptRegistry.render` interpolated values with `str.format`. Keeping
untrusted data inside a delimited block was each template's job: the platform's
demo prompt wrote `<input>{input}</input>` itself. A template that forgot the
block, or a value that contained `</input>`, ended containment, and the rest of
the value read as the prompt's own instructions.

## Decision

- The registry wraps every interpolated value in
  `<input name="<variable>">…</input>`, with `&`, `<` and `>` HTML-escaped, so
  a value can neither close its block nor open another.
- A template opts a variable out through `raw_variables: {name: reason}`. Only
  a value code builds belongs there (a date the host formats, an identifier).
  An unknown name, a blank reason, or a template that writes `<input` itself is
  refused when the artifact is loaded.
- The platform's own prompts declare no raw variable; a test fails the day one
  does, so adding one is a reviewed edit.

## Consequences

- A product's prompt that wrapped its own block must drop the block in a new
  version, or it fails to load.
- What reaches a raw variable is the caller's choice and cannot be checked from
  the artifact. The reason field and the review are the control there.
- Escaping changes the bytes the model sees (`&lt;` for `<`); models read the
  entities, and the trade buys containment that does not depend on wording.
