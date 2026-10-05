# Ops hardening

> **Inherited from `codebase`** (platform seed, `main` `bf553f4`, merged into this
> product repo as `platform/main`). It changes here only when work in this repo
> touches the area; after each `git merge platform/main` the upstream copy is the
> reference. Product work lives in `.claude/plans/supply-chain.md`.

A platform status review on 2026-09-22 found six gaps an enterprise buyer would
ask about. Đạt chose to close five of them before picking the first bounded
context. All five are done and on `main`. The full narrative is at
`git show 84e3f6a:.claude/PLAN.md`, sections "Ops hardening".

| Phase | Commit    | What                                                                        |
| ----- | --------- | --------------------------------------------------------------------------- |
| 1     | `091eb35` | Backup off-box copy (the run fails if upload fails); restore drill run live |
| 2     | `c97ee4e` | `retention@1.4.0.yaml`: `audit.enforced: true`, gated on phase 1            |
| 3     | `061267b` | Spend guard, mechanism only (`platform.tenant_daily_spend_guard`)           |
| 4     | `89b00b5` | Tenant offboarding + data export, end to end                                |
| 5     | `8cbbee6` | Prometheus + Alertmanager, verified against running containers              |
| —     | `2a87f58` | Image pins bumped; real CVE counts recorded                                 |

## What holds, and where it lives

- **One retention file.** `configs/policies/retention@1.4.0.yaml` feeds the
  memory, knowledge and audit sweeps on the worker's hourly lanes.
    - Audit keeps 1095 days: Commercial Law Art. 319's two-year limitation plus a
      review margin. Decree 13/2023 requires deletion once the purpose ends.
    - `legal_hold` is never swept, and a class this build does not know is kept.
- **Partition creation is one function**, `platform.ensure_time_partitions`
  (CLAUDE.md, Data model rules). A partition created later inherits neither
  RLS nor the audit revoke.
- **Offboarding discovers tables from the catalog** (`pg_policies`), not from a
  hand list.
    - `platform.audit_events` is exported but never purged.
    - The one FK chain is purged in hand-checked order.
    - Stale claims older than 30 minutes are reclaimed.
    - Feedback attachments live under the `feedback/{tenant_id}/` prefix.
- **Metrics:**
    - a Prometheus reader is always installed;
    - `dw-api` serves `/metrics` at the root;
    - `dw-worker` serves its own scrape port (9464);
    - six alert rules, each against a metric the code really emits.
- **Object storage is SeaweedFS** (`chrislusf/seaweedfs:4.48`, `weed mini`;
  compose service `s3`), since 2026-09-29.
    - MinIO withdrew its images from Docker Hub and then quay.io. Đạt chose
      replacing it over mirroring the old image.
    - The code speaks plain S3, so the swap was compose-only.
    - Buckets and the backup scripts use `rclone/rclone:1.75.1`, a generic
      client, so the next swap touches neither.
    - A replacement must first pass
      `dw_knowledge/tests/integration/test_object_storage_contract.py`
      (5 tests). The last of them refuses unsigned requests: without
      credentials SeaweedFS allows everything, measured at 200 on an unsigned
      PUT.
    - The earlier `SignatureDoesNotMatch` failures in the `dw_knowledge`
      suite no longer reproduce: 37/37 passed on MinIO and on SeaweedFS.

## Open

- **Offboarding loses in-app notifications unexported** (measured 2026-10-03,
  `workspace-scope-offboarding/01` step 4). `platform.notifications` is read
  only by its recipient (`app.user_id`), so the lane's export lists the table
  with 0 rows and its purge deletes none; the purge of `platform.workspaces`
  then CASCADEs them away. Measured on the test database: a tenant with one
  notification exported `[0]` rows and kept none after purge. The workspace
  scope does not help, since the policy narrows by user, not workspace. Needs
  an export path that reads them (a scope the lane sets, like the workspace
  one, under the same tenant-clause rule) before a real offboarding.
- **Spend guard quotas are unset.** Every plan's `spend_usd_per_day` is `None`,
  so nothing is metered until Đạt gives dollar thresholds.
- **Offboarding export bundles in `dw-exports` are never deleted.** They hold a
  tenant's full data, PII included, and need a retention term from Đạt.
- **Superseded documents:** how many versions to keep is undecided. That is a
  different question from how long a deletion takes to become final.
- **The restore drill is same-cluster only.** A from-scratch DR drill (new
  host, roles provisioned from zero) has not been done.
- **Alert thresholds are starting points, not measurements:** outbox backlog
  500, reaper 20/hour, run failure rate 0.2/s, DB connections 80%.
- **The reaper alert cannot fire.** No bounded context registers a
  `ReapTarget`; this checkout ships none (checked 2026-09-29).
- **`dw_node_failure_total` is declared and emitted by nothing**
  (`dw_observability/metrics.py`), so no rule uses it.
- **No Grafana dashboards.**
- **Accepted CVEs.** About 39–44 HIGH/CRITICAL remain per image, mostly
  transitive Go stdlib/x code. Pins: `prometheus:v3.6.0` (a newer tag did not
  help), `postgres-exporter:v0.17.1`, `alertmanager:v0.28.1`. Re-scan on every
  bump.
    - `chrislusf/seaweedfs:4.48`: none (trivy 0.74.0, 2026-09-29).
    - `rclone/rclone:1.75.1` carries 3 fixable HIGH, all denial of service:
      CVE-2026-14456 in libcrypto3 (twice) and CVE-2026-84445 in grpc.
      Accepted: rclone runs as a one-shot client on the internal network and
      opens no listener. Move to the next rclone release that clears them.
