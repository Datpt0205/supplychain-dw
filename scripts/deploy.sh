#!/usr/bin/env bash
# In-place deploy on a host that already holds the repo, the .env and the volumes.
#
#   scripts/deploy.sh <branch> [<environment>] [hosted]
#
# environment is one of: dev | uat | production. It selects the compose overlay,
# and the overlay is what pins DW_API_PROFILE — so the environment a host runs
# as is decided by the argument to this script, never by whether somebody set a
# variable in .env correctly.
#
# `hosted` adds infra/compose/docker-compose.host.yml last (Caddy on three
# hostnames, ADR 0023; runbook docs/deploy/host.md): base, then the profile
# overlay, then the host overlay. uat and production only. Also an argument,
# not something read from .env, for the same reason.
#
# The same script runs from GitHub Actions and by hand. It lived
# as four near-identical copies of this shell before; four copies drift, and the
# one that drifts is discovered during an incident.
set -euo pipefail

BRANCH="${1:?usage: deploy.sh <branch> [dev|uat|production] [hosted]}"
ENVIRONMENT="${2:-dev}"
EXPOSURE="${3:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/ubuntu/base_agent}"
COMPOSE_BASE="infra/compose/docker-compose.yml"

case "$ENVIRONMENT" in
  dev) OVERLAY="" ;;
  uat) OVERLAY="-f infra/compose/docker-compose.uat.yml" ;;
  production) OVERLAY="-f infra/compose/docker-compose.prod.yml" ;;
  *) echo "unknown environment: $ENVIRONMENT (expected dev, uat or production)" >&2; exit 2 ;;
esac

# The services whose health the deploy waits for.
SERVICES=(api worker web)
case "$EXPOSURE" in
  "") ;;
  hosted)
    if [ "$ENVIRONMENT" = "dev" ]; then
      echo "hosted is for uat or production; dev runs without the Caddy overlay" >&2
      exit 2
    fi
    OVERLAY="$OVERLAY -f infra/compose/docker-compose.host.yml"
    SERVICES+=(caddy)
    ;;
  *) echo "unknown exposure: $EXPOSURE (expected nothing or hosted)" >&2; exit 2 ;;
esac

cd "$DEPLOY_DIR"

echo ">> environment : $ENVIRONMENT${EXPOSURE:+ ($EXPOSURE)}"
echo ">> branch      : $BRANCH"
git fetch origin --quiet
git checkout -B "$BRANCH" "origin/$BRANCH"
git reset --hard "origin/$BRANCH"
echo ">> HEAD        : $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

# shellcheck disable=SC2086  # OVERLAY is a deliberate word-split of compose flags
COMPOSE=(docker compose --env-file .env -f "$COMPOSE_BASE" $OVERLAY)

"${COMPOSE[@]}" --profile full up --build -d

# Migrations run as the one-shot `migrate` service inside `up`. It exits 0 and
# is not part of the health gate below, which watches only the long-lived apps.
# Asked by SERVICE name: container names carry COMPOSE_PROJECT_NAME (this
# product's is `dw_elmichs-api-1`), so a pattern on `dw-api-1` matched nothing
# and the gate passed before anything was up. A service that is not running at
# all is not healthy either.
echo ">> waiting for ${SERVICES[*]} to report healthy..."
DEADLINE=$((SECONDS + 300))
while [ "$SECONDS" -lt "$DEADLINE" ]; do
  status=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Status}}' "${SERVICES[@]}" || true)
  unhealthy=""
  for service in "${SERVICES[@]}"; do
    line=$(printf '%s\n' "$status" | grep -E "^$service " || true)
    case "$line" in
      *"(healthy)"*) ;;
      *) unhealthy="$unhealthy${line:-$service not running}"$'\n' ;;
    esac
  done
  if [ -z "$unhealthy" ]; then
    echo ">> all healthy:"
    printf '%s\n' "$status"
    exit 0
  fi
  sleep 5
done

echo ">> TIMED OUT. Still unhealthy:" >&2
echo "$unhealthy" >&2
"${COMPOSE[@]}" logs --no-color --tail 120 "${SERVICES[@]}" migrate >&2 || true
exit 1
