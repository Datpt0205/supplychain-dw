# Issue tracker: `.claude/plans/` (custom, local markdown)

Work for this repo is tracked in `.claude/plans/`, the plan system the
session hooks already read and enforce. There is no second tracker: a spec or
ticket that lived anywhere else would drift from the plan the hooks check
(`.claude/rules/failure-modes.md` #2).

When the branch is pushed and the team works on GitHub, switch this file to
the GitHub template (re-run `/setup-matt-pocock-skills`) and move open tickets
over; do not run both.

The skills that read this file come from the `mattpocock-skills` plugin, which
is installed per checkout, not by the repository (`CLAUDE.md` "Agent skills";
`claude plugin list` shows "failed to load" where it is enabled but not
installed). Without it, this layout is still the plan the session hooks read.

## Layout

- `.claude/PLAN.md` is the index: where each area stands, what is next, what
  Đạt still owes. `session-start.sh` reads it whole and warns past 80 lines.
- `.claude/plans/<area>.md` is one file per area (`ops-hardening`,
  `platform-runtime`, `web-ui`): state, slice log, open items,
  decisions recorded. It stays the summary of the area.
- Feature work lives under the area it belongs to:
    - the spec: `.claude/plans/<area>/<feature-slug>/spec.md`
    - one file per ticket: `.claude/plans/<area>/<feature-slug>/issues/<NN>-<slug>.md`,
      numbered from `01`, never a single combined tickets file
- A ticket records its triage state as a `Status:` line near the top (role
  strings in `triage-labels.md`), blockers as `Blocked by: NN, NN`, and
  conversation under a `## Comments` heading at the bottom.

## When a skill says "publish to the issue tracker"

Create the file under `.claude/plans/<area>/<feature-slug>/` (creating the
directory if needed), then add or refresh a one-line pointer to it in
`.claude/plans/<area>.md`. The area file is what the stop hook counts as the
plan being updated; a ticket file alone is detail, not the plan.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or
the ticket number directly.

## Closing work

When a ticket is done, set `Status: resolved`, and record the slice (commit
hash, one line) in the area file's slice log, as every slice so far has been.
Update `.claude/PLAN.md` only when an area's state, the next step or a
decision owed changes.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.claude/plans/<area>/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.claude/plans/<area>/<effort>/issues/NN-<slug>.md`, numbered from `01`,
  with the question in the body. A `Type:` line records the ticket type
  (`research`/`prototype`/`grilling`/`task`); a `Status:` line records
  `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked
  when every file it lists is `resolved`.
- **Frontier**: scan `.claude/plans/<area>/<effort>/issues/` for files that are open,
  unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set
  `Status: resolved`, then append a context pointer (gist + link) to the map's
  Decisions-so-far in `map.md`.
