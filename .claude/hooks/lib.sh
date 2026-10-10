# Shared by the hooks in this directory. Sourced, never run on its own.
# Tested by scripts/test_claude_hooks.sh.

# The hook's JSON payload, read once from stdin. A hook run by hand from a
# terminal has no payload, and must not sit waiting for one.
read_hook_input() {
  hook_input=""
  [ -t 0 ] || hook_input=$(cat)
}

# hook_string <jq path> <key>: one string field of the payload. jq when it is
# installed; otherwise sed and awk, because a hook that needs jq does nothing on
# a machine without it (Git Bash ships none). The fallback reads the last
# "<key>": "<string>" on a line and undoes JSON's escapes; an escaped quote inside
# the value cannot end the match, so text in a command that looks like a key
# does not either.
hook_string() {
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$hook_input" | jq -r "$1 // \"\" | strings"
    return
  fi
  printf '%s' "$hook_input" \
    | sed -nE 's/.*"'"$2"'"[[:space:]]*:[[:space:]]*"(([^"\\]|\\.)*)".*/\1/p' \
    | head -1 \
    | awk '{
        out = ""; n = length($0)
        for (i = 1; i <= n; i++) {
          c = substr($0, i, 1)
          if (c == "\\" && i < n) {
            d = substr($0, ++i, 1)
            if (d == "n") c = "\n"; else if (d == "t") c = "\t"; else if (d == "r") c = "\r"
            else if (d == "u") { c = "?"; i += 4 } else c = d
          }
          out = out c
        }
        printf "%s", out
      }'
}

# The file holding where this session started (see session-start.sh), or nothing
# when the payload carries no usable session id. The id is reduced to the
# characters a session id has, so it can only ever name a file in .claude/.
session_mark() {
  local id
  id=$(hook_string '.session_id' session_id | tr -cd 'A-Za-z0-9_-')
  [ -n "$id" ] && printf '%s/.claude/.session-head.%s' "$root" "$id"
}

# Every path that is not as HEAD has it (staged, unstaged, deleted, untracked and
# not ignored), one per line as "<content hash><TAB><path>", "-" for a path with
# no file. Content, not status: a file already modified when the session began
# and modified again since reads as changed, which `git status` alone cannot say.
worktree_state() {
  local paths existing missing
  local own=':(exclude,glob).claude/.session-head*' # this state, never the session's work
  paths=$( { git -c core.quotePath=false diff --name-only HEAD -- . "$own" 2>/dev/null
             git -c core.quotePath=false ls-files --others --exclude-standard -- . "$own"; } | sort -u)
  [ -z "$paths" ] && return 0
  existing=$(printf '%s\n' "$paths" | while IFS= read -r p; do [ -f "$p" ] && printf '%s\n' "$p"; done)
  missing=$(printf '%s\n' "$paths" | while IFS= read -r p; do [ -f "$p" ] || printf -- '-\t%s\n' "$p"; done)
  if [ -n "$existing" ]; then
    paste <(printf '%s\n' "$existing" | git hash-object --stdin-paths) <(printf '%s\n' "$existing")
  fi
  [ -n "$missing" ] && printf '%s\n' "$missing"
  return 0
}
