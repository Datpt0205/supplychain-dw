#!/usr/bin/env bash
# Stop hook. Runs when the model finishes a turn. Exit 2 sends stderr back as
# feedback and asks it to keep working; exit 0 lets the turn end.
# Tested by scripts/test_claude_hooks.sh.
#
# What it is for: a session that ends with the work committed but the plan
# describing the previous state has lost what it learned, however good the code is.
# The next session reads the plan and believes it. The plan is `.claude/PLAN.md`
# (the index) plus one file per area under `.claude/plans/`; updating either counts.
#
# It counts THIS session's work only: what changed since the baseline
# session-start.sh wrote for this session id. Files that were already uncommitted
# when the session began belong to someone else (another session, a parallel
# workflow, a person) and it does not ask for them to be committed.
#
# To disable: a PERSON creates .claude/no-stop-gate (gitignored, so it switches
# the hook off in this checkout only). An agent never creates it.
set -uo pipefail

root="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$root" || exit 0
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ -f "$root/.claude/no-stop-gate" ] && exit 0

read_hook_input

# Guard against a loop: if this hook already fired for this turn, let it end.
# jq is preferred; the grep fallback keeps the guard working without it — a hook
# that fails open into an infinite loop is worse than one that does not run.
if command -v jq >/dev/null 2>&1; then
  active=$(printf '%s' "$hook_input" | jq -r '.stop_hook_active // false')
else
  active=$(printf '%s' "$hook_input" | grep -o '"stop_hook_active"[[:space:]]*:[[:space:]]*true' >/dev/null && echo true || echo false)
fi
[ "$active" = "true" ] && exit 0

git rev-parse --git-dir >/dev/null 2>&1 || exit 0

mark=$(session_mark)
started=""
already=0
if [ -n "$mark" ] && [ -f "$mark" ]; then
  touch "$mark" # still in use: session-start.sh expires baselines by age
  started=$(sed -n '1s/^head //p' "$mark")
  current=$(worktree_state)
  # A line is "<hash><TAB><path>"; one the baseline already holds is untouched
  # since the session began.
  ours=$(printf '%s\n' "$current" | grep -vxF -f <(sed 1d "$mark") | grep . | cut -f2-)
  [ -n "$current" ] && already=$(printf '%s\n' "$current" | grep -cxF -f <(sed 1d "$mark"))
  baseline="known"
else
  ours=$(worktree_state | cut -f2-)
  baseline="none"
fi

# Nothing uncommitted from this session, but did it COMMIT something without
# saying what it learned? That is the quieter half of the same loss: the code is
# safe, the reasoning behind it is not, and the next session reads a plan
# describing the state before any of it happened.
#
# A commit carries no session id, so another session's commit on this branch
# since the baseline counts here too. Uncommitted files can be told apart by
# content; commits cannot.
if [ -z "$ours" ]; then
  [ -z "$started" ] && exit 0
  git cat-file -e "$started^{commit}" 2>/dev/null || exit 0
  [ "$started" = "$(git rev-parse HEAD)" ] && exit 0
  if git diff --name-only "$started"..HEAD | grep -qE '^\.claude/(PLAN\.md|plans/[^/]+\.md)$'; then
    exit 0
  fi
  {
    echo "This session committed $(git rev-list --count "$started"..HEAD) change(s)"
    echo "and touched neither .claude/PLAN.md nor an area file in .claude/plans/."
    echo
    echo "The code is safe; what was learned making it is not. Update the plan so"
    echo "the next session reads where the work actually stands — what is done,"
    echo "what is open, what was measured and found NOT to be true, and any"
    echo "decision the user still owes. Then commit that too."
    echo
    echo "If this session genuinely learned nothing worth recording, say so and"
    echo "end the turn; this fires once."
  } >&2
  exit 2
fi

count=$(printf '%s\n' "$ours" | grep -c .)
{
  if [ "$baseline" = "none" ]; then
    echo "There are $count uncommitted file(s). This hook has no baseline for this"
    echo "session (no session id, or SessionStart did not record one), so it cannot"
    echo "tell this session's files from ones that were dirty before it began; the"
    echo "list below may include work that is not yours."
  else
    echo "This session left $count uncommitted file(s):"
  fi
  printf '%s\n' "$ours" | head -20 | sed 's/^/  /'
  [ "$count" -gt 20 ] && echo "  … and $((count - 20)) more"
  if [ "$already" -gt 0 ]; then
    echo "($already other file(s) were already uncommitted when this session began"
    echo "and are unchanged since; they are not this session's, so leave them be.)"
  fi
  echo "Before ending the turn: commit them with a Conventional Commits message,"
  echo "and update the plan so it describes where the work now stands — what was"
  echo "done, what is open, and any decision the user still owes: the area file in"
  echo ".claude/plans/, and .claude/PLAN.md (the index) if the state, the next"
  echo "step or a decision owed changed."
  echo "If the work is deliberately incomplete, say so explicitly rather than"
  echo "committing it as if it were finished."
} >&2
exit 2
