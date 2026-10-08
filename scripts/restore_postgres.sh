#!/usr/bin/env bash
# Restore a Postgres dump produced by scripts/backup_postgres.sh.
#
# The mechanism (dump -> restore -> migrate heads) is rehearsed on every CI
# push by
# packages/python/dw_platform/tests/integration/test_restore_drill.py.
# Running this script for real, against this host, is a separate act a human
# still has to do at least once — a CI pass proves the round-trip works, not
# that anyone here has typed the command under pressure.
#
# Usage:
#   scripts/restore_postgres.sh <local-dump.dump.gz>
#   scripts/restore_postgres.sh --latest        # pull the newest object from
#                                                # the off-box S3 bucket
#
# --clean --if-exists: pg_restore drops each object before recreating it, so
# this is safe to run against a database that already has the (possibly
# stale or corrupt) schema in it. It is NOT additive — anything in the target
# database that is not in the dump stays; anything the dump recreates
# overwrites what was there.
set -euo pipefail

# This checkout's own container: compose names it after the project, and
# a second dw-based checkout on the same host owns "dw-postgres-1".
CONTAINER="${PG_CONTAINER:-${COMPOSE_PROJECT_NAME:-dw}-postgres-1}"
DB="${PG_DB:-dw}"
PG_USER="${PG_USER:-dw_admin}"
DEST="${BACKUP_DIR:-/home/ubuntu/pg_backups}"
MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://127.0.0.1:9000}"
S3_BUCKET_PG_BACKUPS="${S3_BUCKET_PG_BACKUPS:-dw-pg-backups}"

# The off-box copy goes through rclone in a container: a generic S3 client,
# so this script depends on no server's own tooling (MinIO's `mc` went away
# with its images), and the host needs nothing installed beyond Docker. Host
# network, so the endpoint is the one this host publishes; the credentials
# reach the container from the environment (`-e NAME` without a value), never
# on a command line where `ps` would show them.
RCLONE_IMAGE="${RCLONE_IMAGE:-rclone/rclone:1.75.1}"
s3() {
  RCLONE_CONFIG_S3_TYPE=s3 RCLONE_CONFIG_S3_PROVIDER=Other   RCLONE_CONFIG_S3_ENDPOINT="$MINIO_ENDPOINT"   RCLONE_CONFIG_S3_ACCESS_KEY_ID="$MINIO_ROOT_USER"   RCLONE_CONFIG_S3_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"   sudo --preserve-env=RCLONE_CONFIG_S3_TYPE,RCLONE_CONFIG_S3_PROVIDER,RCLONE_CONFIG_S3_ENDPOINT,RCLONE_CONFIG_S3_ACCESS_KEY_ID,RCLONE_CONFIG_S3_SECRET_ACCESS_KEY     docker run --rm --network host -v "$DEST:$DEST"       -e RCLONE_CONFIG_S3_TYPE -e RCLONE_CONFIG_S3_PROVIDER -e RCLONE_CONFIG_S3_ENDPOINT       -e RCLONE_CONFIG_S3_ACCESS_KEY_ID -e RCLONE_CONFIG_S3_SECRET_ACCESS_KEY       "$RCLONE_IMAGE" -q "$@"
}

usage() {
  echo "usage: $0 <local-dump.dump.gz> | --latest" >&2
  exit 2
}

[ $# -eq 1 ] || usage

if [ "$1" = "--latest" ]; then
  if [ -z "${MINIO_ROOT_USER:-}" ] || [ -z "${MINIO_ROOT_PASSWORD:-}" ]; then
    echo "MINIO_ROOT_USER/MINIO_ROOT_PASSWORD not set; cannot fetch --latest" >&2
    exit 1
  fi
  mkdir -p "$DEST"
  # Names are `<db>_<YYYYmmdd_HHMMSS>.dump.gz`, so the lexical last is the newest.
  # The bucket holds every database's dumps (dw_…, keycloak_…): the newest of
  # THIS one, never whichever database happened to be dumped last.
  latest="$(s3 lsf "s3:$S3_BUCKET_PG_BACKUPS/" | grep "^${DB}_" | sort | tail -n1 || true)"
  [ -n "$latest" ] || { echo "no objects found in s3:$S3_BUCKET_PG_BACKUPS/" >&2; exit 1; }
  dump="$DEST/$latest"
  echo "[$(date -Is)] fetching s3:$S3_BUCKET_PG_BACKUPS/$latest -> $dump"
  s3 copyto "s3:$S3_BUCKET_PG_BACKUPS/$latest" "$dump"
else
  dump="$1"
  [ -f "$dump" ] || { echo "no such file: $dump" >&2; exit 1; }
fi

echo "[$(date -Is)] restoring $dump into $CONTAINER:$DB as $PG_USER"
gunzip -c "$dump" | sudo docker exec -i "$CONTAINER" pg_restore -U "$PG_USER" -d "$DB" --clean --if-exists
echo "[$(date -Is)] restore ok"

# Confirms the restored dump is at a single, known migration head before
# traffic resumes — the same check CI runs on every push (ci.yml, "Migration
# chain is linear"). Requires running from a repo checkout with uv installed
# and DW_DATABASE_URL pointed at $DB; that is the same precondition deploy.sh
# already has.
if command -v uv >/dev/null 2>&1 && [ -f "db/alembic.ini" ]; then
  echo "[$(date -Is)] checking alembic head"
  uv run alembic -c db/alembic.ini heads
else
  echo "[$(date -Is)] uv or db/alembic.ini not found here — run" \
    "'uv run alembic -c db/alembic.ini heads' from the repo checkout" \
    "against this database before resuming traffic" >&2
fi
