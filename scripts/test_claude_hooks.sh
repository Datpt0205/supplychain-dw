#!/usr/bin/env bash
# Tests for the coding-agent harness in .claude/ (hooks, their registration and
# the gitignore lines behind them). Runs on Git Bash and on Linux, with or without
# jq: the hooks fall back to sed/awk when jq is missing, so each machine tests the
# path it actually runs.
#
#   bash scripts/test_claude_hooks.sh      (also: make test-hooks, part of make ci)
#
# Every case drives a hook the way Claude Code does — JSON on stdin,
# CLAUDE_PROJECT_DIR set — against a throwaway repository, never this checkout.
# The commit gate is run with a `uv` on PATH that always fails, which is how a
# broken invariant looks to it, without touching scripts/verify_invariants.py.
set -uo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
hooks="$repo/.claude/hooks"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

export GIT_AUTHOR_NAME=hooktest GIT_AUTHOR_EMAIL=hooktest@example.invalid
export GIT_COMMITTER_NAME=hooktest GIT_COMMITTER_EMAIL=hooktest@example.invalid
export GIT_CONFIG_NOSYSTEM=1

passed=0
failed=0
check() { # description expected actual
  if [ "$2" = "$3" ]; then
    passed=$((passed + 1))
  else
    failed=$((failed + 1))
    echo "FAIL: $1 (expected $2, got $3)"
  fi
}

json_string() { # a bash string as a JSON string literal
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' \
    | awk 'BEGIN { printf "\"" } { printf "%s%s", (NR > 1 ? "\\n" : ""), $0 } END { printf "\"" }'
}

new_repo() { # path
  git init -q "$1"
  printf 'one\n' > "$1/a.txt"
  git -C "$1" add a.txt
  git -C "$1" commit -q -m init
}

# --- the commit gate -----------------------------------------------------------

mkdir -p "$work/bin"
printf '#!/usr/bin/env bash\necho "fake invariant: broken" >&2\nexit 1\n' > "$work/bin/uv"
chmod +x "$work/bin/uv"
new_repo "$work/gate"

gate() { # tool command -> the gate's exit code
  printf '{"session_id":"t","hook_event_name":"PreToolUse","tool_name":"%s","tool_input":{"command":%s,"description":"x"}}' \
    "$1" "$(json_string "$2")" \
    | CLAUDE_PROJECT_DIR="$work/gate" PATH="$work/bin:$PATH" bash "$hooks/pre-commit-gate.sh" >/dev/null 2>&1
  echo $?
}

gated=(
  'git commit -m x'
  'git -C . commit -m x'
  'git -C "dir with space" commit -m x'
  'git -c user.name=x commit -m x'
  'git -c user.name=x -c commit.gpgsign=false commit -m x'
  'git --no-pager commit -m x'
  'git --git-dir=.git --work-tree=. commit -m x'
  'git --git-dir .git commit'
  'cd sub && git commit -m x'
  'git add -A; git commit -m x'
  'git status | cat && git commit --amend --no-edit'
  'git.exe commit -m x'
  "git commit -m @'
first line
'@"
  'git add a.txt
git commit -F msg.txt'
  'git commit'
  'Git commit -m x'
  'GIT.EXE commit -m x'
  'git \
  commit -m x'
  'git -C . `
  commit -m x'
)
not_gated=(
  'git commit-tree HEAD^{tree}'
  'git log --grep commit'
  'echo "git commit"'
  'git status'
  'ls commit'
  'gitk commit'
)
for tool in Bash PowerShell; do
  for cmd in "${gated[@]}"; do
    check "$tool gates: $cmd" 2 "$(gate "$tool" "$cmd")"
  done
  for cmd in "${not_gated[@]}"; do
    check "$tool lets through: $cmd" 0 "$(gate "$tool" "$cmd")"
  done
done

# A payload the hook cannot parse (here: the string never closes) is gated on its
# raw text, never let through because no command could be read from it.
unreadable=$(printf '{"tool_name":"Bash","tool_input":{"command":"git commit -m x' \
  | CLAUDE_PROJECT_DIR="$work/gate" PATH="$work/bin:$PATH" bash "$hooks/pre-commit-gate.sh" >/dev/null 2>&1
  echo $?)
check "an unreadable payload naming a commit is gated" 2 "$unreadable"

# The hook is only as wide as the matcher that runs it: a PowerShell commit never
# reaches a hook registered for Bash alone.
matcher=$(awk '/"matcher"/ { m = $0 } /pre-commit-gate\.sh/ { print m; exit }' "$repo/.claude/settings.json" \
  | sed -E 's/.*"matcher"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/')
for tool in Bash PowerShell; do
  printf '%s' "$tool" | grep -qxE "$matcher"
  check "settings.json registers the gate for $tool (matcher '$matcher')" 0 $?
done

# A gate switch committed by accident would switch the gate off in every clone.
for path in .claude/no-commit-gate .claude/no-stop-gate .claude/.session-head.abc123; do
  git -C "$repo" check-ignore -q "$path"
  check "$path is gitignored" 0 $?
done

# --- the session baseline and the Stop hook --------------------------------------

s="$work/session"
new_repo "$s"
start() { # session_id source
  printf '{"session_id":"%s","hook_event_name":"SessionStart","source":"%s"}' "$1" "$2" \
    | CLAUDE_PROJECT_DIR="$s" bash "$hooks/session-start.sh" >/dev/null 2>&1
}
stop() { # session_id -> exit code; stderr kept in $work/stop.err
  printf '{"session_id":"%s","hook_event_name":"Stop","stop_hook_active":false}' "$1" \
    | CLAUDE_PROJECT_DIR="$s" bash "$hooks/session-stop.sh" >/dev/null 2>"$work/stop.err"
  echo $?
}
said() { grep -qF -- "$1" "$work/stop.err" && echo yes || echo no; }

# Dirt from before the session: a modified tracked file and an untracked one.
printf 'two\n' >> "$s/a.txt"
printf 'pre\n' > "$s/pre.txt"
cp "$s/a.txt" "$work/a.before"

start A startup
check "a baseline file per session" yes "$([ -f "$s/.claude/.session-head.A" ] && echo yes || echo no)"
check "dirt from before the session, session changed nothing: turn ends" 0 "$(stop A)"

printf 'new\n' > "$s/new.txt"
check "session added a file: asked to commit" 2 "$(stop A)"
check "...and the message names it" yes "$(said new.txt)"
check "...and does not name the dirt from before" no "$(said pre.txt)"
rm "$s/new.txt"

printf 'three\n' >> "$s/a.txt"
check "session changed a file that was already dirty: asked" 2 "$(stop A)"
check "...naming it" yes "$(said a.txt)"
cp "$work/a.before" "$s/a.txt"
check "file back as it was at session start: turn ends" 0 "$(stop A)"

# Two sessions in one checkout: each sees its own work, not the other's.
printf 'a2\n' > "$s/a2.txt"
start B startup
check "a later session does not own an earlier session's file" 0 "$(stop B)"
check "the earlier session still does" 2 "$(stop A)"
check "...naming it" yes "$(said a2.txt)"
rm "$s/a2.txt"

# A commit made before a compaction is still seen after it.
printf 'c\n' > "$s/c.txt"
git -C "$s" add c.txt && git -C "$s" commit -q -m "feat: c"
start A compact
check "commit without a plan update, after a compaction: asked" 2 "$(stop A)"
check "...counting the commit" yes "$(said "committed 1 change(s)")"
mkdir -p "$s/.claude"
printf '# plan\n' > "$s/.claude/PLAN.md"
git -C "$s" add .claude/PLAN.md && git -C "$s" commit -q -m "docs(plan): c"
check "plan updated in a commit: turn ends" 0 "$(stop A)"

# No baseline: the old behaviour, but saying it cannot tell.
check "no baseline and a dirty tree: asked" 2 "$(stop nobaseline)"
check "...and says it has no baseline" yes "$(said "no baseline")"

active=$(printf '{"session_id":"A","stop_hook_active":true}' \
  | CLAUDE_PROJECT_DIR="$s" bash "$hooks/session-stop.sh" >/dev/null 2>&1; echo $?)
check "stop_hook_active: the hook fires once per turn" 0 "$active"

# Baselines expire; one a session is still using does not (its Stop touches it).
touch -d '10 days ago' "$s/.claude/.session-head.A" "$s/.claude/.session-head.B"
stop A >/dev/null
start C startup
check "a baseline untouched for 7 days is removed" no "$([ -f "$s/.claude/.session-head.B" ] && echo yes || echo no)"
check "one whose session is still stopping turns is kept" yes "$([ -f "$s/.claude/.session-head.A" ] && echo yes || echo no)"

# A session id is reduced to [A-Za-z0-9_-] before it names a file.
start 'ab/../cd' startup
check "a session id with path characters names one plain file" yes "$([ -f "$s/.claude/.session-head.abcd" ] && echo yes || echo no)"

echo "hooks: $passed passed, $failed failed"
[ "$failed" -eq 0 ]
