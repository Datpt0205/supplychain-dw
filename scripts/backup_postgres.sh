#!/usr/bin/env bash
# Nightly Postgres backup — local dump plus an off-box copy to the S3 object
# store this stack already runs.
#
# Why this exists (C2): the database lived on a single Docker volume with NO
# backup — a bad migration, an accidental `down -v`, or disk loss meant total,
# unrecoverable loss of every tenant's CRM data. This gives a daily recovery
# point. A dump on the same box does not survive the box itself, so the S3
# copy below is not optional — it is what makes this a backup rather than a
# second copy of the same failure.
#
# Restore: scripts/restore_postgres.sh (same directory) — it pulls the dump
# back from the object store (or takes a local path) and runs the pg_restore for you.
# The mechanism is rehearsed on every CI push by
# packages/python/dw_platform/tests/integration/test_restore_drill.py, which
# proves dump -> restore -> migrate-heads round-trips; that is not the same
# claim as a human having run restore_postgres.sh against this specific host.
#
# Two databases, one run: `dw` (every tenant's data) and `keycloak` (every user,
# credential and the realm). Losing the second is losing every login, so it is
# dumped by default, not by a second cron line somebody has to remember. Each
# database's dumps carry its own name (`dw_…`, `keycloak_…`), rotate on their
# own, and restore one at a time (`PG_DB=keycloak scripts/restore_postgres.sh …`).
#
# Install (on the server, as the ubuntu user), one line for both databases:
#   crontab -e   →   15 2 * * *  cd /home/ubuntu/base_agent && set -a && . ./.env && set +a && scripts/backup_postgres.sh >> /home/ubuntu/pg_backups/backup.log 2>&1
# (`.env` supplies COMPOSE_PROJECT_NAME, MINIO_ROOT_USER and MINIO_ROOT_PASSWORD.)
set -euo pipefail

# This checkout's own container: compose names it after the project, and
# a second dw-based checkout on the same host owns "dw-postgres-1".
CONTAINER="${PG_CONTAINER:-${COMPOSE_PROJECT_NAME:-dw}-postgres-1}"
# PG_DBS lists the databases; PG_DB (one name) is kept for a manual run.
DBS="${PG_DBS:-${PG_DB:-dw keycloak}}"
PG_USER="${PG_USER:-dw_admin}"
DEST="${BACKUP_DIR:-/home/ubuntu/pg_backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
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

mkdir -p "$DEST"

# The off-box copy is required (below), so its credentials are checked before
# any dump starts rather than after the first one is already on disk.
if [ -z "${MINIO_ROOT_USER:-}" ] || [ -z "${MINIO_ROOT_PASSWORD:-}" ]; then
  echo "[$(date -Is)] MINIO_ROOT_USER/MINIO_ROOT_PASSWORD not set; refusing to" \
    "finish with a local-only backup" >&2
  exit 1
fi

stamp="$(date +%Y%m%d_%H%M%S)"
for DB in $DBS; do
  out="$DEST/${DB}_${stamp}.dump.gz"

  echo "[$(date -Is)] backup start → $out"
  # -Fc = custom format (compressible, selective restore); piped through gzip.
  # A tmp file + atomic mv so a crashed dump never leaves a truncated "backup".
  tmp="$out.partial"
  if sudo docker exec "$CONTAINER" pg_dump -U "$PG_USER" -Fc "$DB" | gzip > "$tmp"; then
    mv "$tmp" "$out"
    echo "[$(date -Is)] backup ok: $(du -h "$out" | cut -f1)"
  else
    rm -f "$tmp"
    echo "[$(date -Is)] backup FAILED: $DB" >&2
    exit 1
  fi

  # Rotate: drop this database's dumps older than KEEP_DAYS. Runs only after a
  # successful dump so a run of failures never deletes the last good copy.
  find "$DEST" -name "${DB}_*.dump.gz" -mtime "+${KEEP_DAYS}" -print -delete

  # Off-box copy — the part that survives losing the box. Required, not
  # best-effort: a failed upload fails the whole run (cron mail/log picks it
  # up) rather than silently leaving last night as the newest off-box copy.
  s3 copyto "$out" "s3:$S3_BUCKET_PG_BACKUPS/$(basename "$out")"
  echo "[$(date -Is)] off-box copy ok: s3:$S3_BUCKET_PG_BACKUPS/$(basename "$out")"
done

echo "[$(date -Is)] done"
