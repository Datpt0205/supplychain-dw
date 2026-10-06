"""Merge the shared-dev `.env.server` template onto a working local `.env`.

The template is the server's config: it points every issuer and public URL at
`sales-dev.dxrank.vn` and carries the server's own infrastructure passwords.
Applied verbatim to a developer machine it breaks three things at once, all of
them silently:

- the Postgres and Keycloak passwords are baked into local volumes at first
  init, and the object store's credentials and endpoint are this machine's own,
  so replacing them makes every connection fail with a password error that
  reads like a code bug;
- `DW_MODEL_PROVIDER=mock` with an empty `OPENAI_API_KEY` sends every model call
  to the deterministic mock, which returns plausible answers and no error;
- the search provider keys are absent from the template, and a lane with no
  provider falls back to the fixed corpus - again, no error.

So the merge rule is one sentence: **take the server's identity and public URLs,
keep everything a running local container or a local-only secret depends on.**
That is what "shared dev" means in this repo - one Keycloak realm for everybody,
each developer's data plane still their own.

Run it again whenever a new template arrives; it is idempotent and always writes
a backup first.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Values whose counterpart lives in a container this machine already created, or
# a secret only this machine has. The template cannot know either.
PRESERVE_LOCAL = frozenset(
    {
        # Baked into the Postgres volume at first init, and the two roles the
        # migration created with them.
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "DW_DB_MIGRATOR_PASSWORD",
        "DW_DB_APP_PASSWORD",
        "DW_DATABASE_URL",
        "DW_API_DATABASE_URL",
        "DW_WORKER_DATABASE_URL",
        "DW_CHAT_DATABASE_URL",
        # The rest of the local data plane.
        "QDRANT_URL",
        "QDRANT_API_KEY",
        # The embedding stack is sized to this machine, and the collection's
        # vector width is baked into the Qdrant volume at first write — the
        # server's provider/collection pair cannot land here without a
        # re-index.
        "QDRANT_COLLECTION",
        "DW_API_EMBEDDING_PROVIDER",
        "REDIS_URL",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "S3_ENDPOINT_URL",
        "KEYCLOAK_ADMIN",
        "KEYCLOAK_ADMIN_PASSWORD",
        "KEYCLOAK_DB_PASSWORD",
        "CLICKHOUSE_PASSWORD",
        "LANGFUSE_NEXTAUTH_SECRET",
        "LANGFUSE_SALT",
        # Signing secrets for dev-mode tokens. Replacing them invalidates every
        # token already issued on this machine.
        "DW_API_DEV_SECRET",
        "DW_CHAT_DEV_SECRET",
        # The model gateway and the search chain. The template leaves both blank
        # because they are per-developer keys, and blank means "mock" rather
        # than "fail", which is the dangerous half.
        "DW_MODEL_PROVIDER",
        "DW_API_MODEL_PROFILE",
        "DW_CHAT_MODEL_PROVIDER",
        "DW_API_OPENAI_STRUCTURED_MODE",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        # The reranker is a per-developer key too; blank means "no reranking",
        # which, like the mock, fails by quietly being worse.
        "DW_API_RERANK_PROVIDER",
        "DW_API_RERANK_BASE_URL",
        "DW_API_RERANK_API_KEY",
        "DW_API_RERANK_MODEL",
        "DW_API_RERANK_TIMEOUT_SECONDS",
        "SERPER_API_KEY",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "SEARXNG_URL",
        "SEARCH_PROVIDER_ORDER",
        "APIFY_API_TOKEN",
        # Where the browser and the tests reach this machine's own API. Taking
        # the server's URLs here would make a local UI test exercise the
        # server's build instead of the working tree.
        "NEXT_PUBLIC_API_BASE_URL",
        "NEXT_PUBLIC_CHAT_BASE_URL",
        "DW_PUBLIC_WEB_URL",
    }
)

_MARKER = "# --- Giu tu .env local (khong co trong template) ---"


def parse_env(text: str) -> dict[str, str]:
    """Every assignment in the file, last occurrence winning.

    Last wins because that is what `set -a; . ./.env` and Docker Compose both
    do, and the template repeats nine keys in its trailing server block.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value
    return values


def merge(template: str, local: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """The template with preserved local values substituted in place.

    Substituted in place rather than appended so the file keeps the template's
    comments and ordering - the comments are how the next person knows which
    half of a pair of URLs is the one that matters.
    """
    kept: list[str] = []
    lines: list[str] = []
    for line in template.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in PRESERVE_LOCAL and key in local:
                lines.append(f"{key}={local[key]}")
                if key not in kept:
                    kept.append(key)
                continue
        lines.append(line)

    template_keys = set(parse_env(template))
    orphans = [key for key in PRESERVE_LOCAL if key in local and key not in template_keys]
    if orphans:
        lines.extend(["", _MARKER])
        lines.extend(f"{key}={local[key]}" for key in sorted(orphans))
    return "\n".join(lines) + "\n", kept, sorted(orphans)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", default="docs-gia/env-server/env.server")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--backup", default=".env.local-backup")
    parser.add_argument(
        "--server-copy",
        default=".env.server",
        help="Where to drop the template verbatim, for deploying to the server",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    template_path = REPO_ROOT / args.template
    env_path = REPO_ROOT / args.env
    if not template_path.exists():
        print(f"khong thay template: {template_path}", file=sys.stderr)
        return 1
    if not env_path.exists():
        print(f"khong thay .env: {env_path}", file=sys.stderr)
        return 1

    template = template_path.read_text(encoding="utf-8")
    local = parse_env(env_path.read_text(encoding="utf-8"))
    merged, kept, orphans = merge(template, local)

    print(f"giu tu local ({len(kept)} key):")
    for key in kept:
        print(f"  {key}")
    if orphans:
        print(f"\nchi co o local, them vao cuoi ({len(orphans)} key):")
        for key in orphans:
            print(f"  {key}")

    server_only = [
        key
        for key, value in parse_env(template).items()
        if key not in PRESERVE_LOCAL and local.get(key) != value
    ]
    print(f"\nlay tu server ({len(server_only)} key):")
    for key in sorted(server_only):
        print(f"  {key}")

    if args.dry_run:
        print("\n--dry-run: khong ghi file nao")
        return 0

    shutil.copyfile(env_path, REPO_ROOT / args.backup)
    env_path.write_text(merged, encoding="utf-8")
    (REPO_ROOT / args.server_copy).write_text(template, encoding="utf-8")
    print(f"\nda sao luu  -> {args.backup}")
    print(f"da ghi      -> {args.env}")
    print(f"da chep     -> {args.server_copy} (nguyen ban template)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
