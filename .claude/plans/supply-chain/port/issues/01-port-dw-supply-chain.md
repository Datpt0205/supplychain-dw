# 01 — Chuyển package, migration, config, wiring của `dw_supply_chain`

Status: ready-for-agent
Blocked by: —
Area: supply-chain

## Mục tiêu

Bước 10–17 (Hồ sơ PO) chạy trên `main` như đã chạy trên nhánh lưu trữ, với mọi cổng
của nền tảng hiện tại (spec, Mục tiêu).

## Việc cần làm

1. **Package:** `git checkout archive-supply-chain -- packages/python/dw_supply_chain`.
2. **Đăng ký ở gốc:** `pyproject.toml` gốc (`tool.uv.sources`, ruff
   `known-first-party`, mypy `files`/`mypy_path`, coverage `source`, import-linter
   `root_packages` và hợp đồng "Supply Chain is independent");
   `scripts/verify_architecture.py` (`IMPORT_TO_DIST`); một dòng COPY trong
   `infra/docker/api.Dockerfile` và `worker.Dockerfile`; `dw-supply-chain` trong
   `apps/api/pyproject.toml`, `apps/worker/pyproject.toml`,
   `packages/python/dw_evals/pyproject.toml`. Rồi `uv lock`.
3. **Migration:** chép 13 file. `fddd7579ba27.down_revision = "855ae928c3fa"`; nối lại
   `1c26d9c88738 → 61d934921951`, `2a0ac1ac32e1 → f35345378e3c`,
   `564975794c7e → 2a0ac1ac32e1`; `dc2285c629d4 → 89e86dfabad6` giữ nguyên. Giữ
   `89e86dfabad6` (không làm gì trên database mới; bỏ nó thì phải nối lại thêm).
4. **Config và eval:** `configs/policies/supply_chain_*`, `configs/prompts/supply_chain/*`,
   `configs/workers/supply_chain_advance_case.yaml`;
   `evals/datasets/supply_chain@1.1.0.json` với 22 fixture, 22 expected, fixture
   mock model; khối grader `supply_chain.case_query_plan`,
   `supply_chain.brief_summary_grounding` trong `dw_evals/graders.py`.
5. **API:** `bootstrap/paths.py`, `container.py` (28 trường), `wiring.py` ở seam
   "BOUNDED CONTEXTS PLUG IN HERE" (gồm
   `strict_approval_prefixes |= {"supply_chain.case_action."}`), mount router trong
   `main.py`, hai test API.
6. **Worker:** `consumers/supply_chain.py`, lane `supply_chain_follow_ups`, setting
   `supply_chain_follow_up_interval_seconds` (mặc định 300) có dòng trong
   `.env.example`, phần thêm của `test_worker.py`.
7. **Web:** contracts `supply_chain.ts` và export, các method supply chain trong
   `api-client/src/client.ts`, mục nav, trang `apps/web/app/supply-chain/*`,
   component `components/supply-chain/*`, `lib/supply-chain/po-case-filter.ts` và
   test của chúng, nguyên trạng.
8. **Sinh lại** `contracts/openapi/openapi.json`, `api-client/src/generated/platform.d.ts`,
   release manifest. Không chép bản cũ của các file sinh ra hay của file "mới hơn trên
   main".

## Tiêu chí chấp nhận

- [ ] Mọi test unit và integration mà nhánh lưu trữ có ở `5d24c25` đều xanh: 21 file
      `tests/unit/test_*.py` và 8 file `tests/integration/test_*.py` (đếm theo
      `test_*.py`, không tính `__init__.py`); ghi số test đã chạy vào Comments.
- [ ] `alembic heads` ra đúng một head; `alembic upgrade head` trên database rỗng rồi
      `downgrade` về `855ae928c3fa` chạy hết.
- [ ] `test_rls_coverage.py` xanh với schema `supply_chain` (đọc từ catalog, không
      danh sách tay); mọi policy thu hẹp theo workspace của context dùng hình
      `tenant AND (workspace OR app.workspace_scope = 'tenant')`.
- [ ] `test_privileges.py` xanh: USAGE trên `supply_chain`, không DELETE/TRUNCATE trên
      `follow_ups` cho `dw_app`.
- [ ] Offboarding xuất và xóa bảng của `supply_chain` (đọc `pg_policies`); một test
      integration chứng minh, nếu chưa có.
- [ ] Test âm xuyên tenant có sẵn trong các test repository của context vẫn chạy và
      xanh; ghi số test vào Comments.
- [ ] `make ci` xanh; `scripts/verify_architecture.py` xanh; `pnpm lint`,
      `pnpm typecheck`, `pnpm build` xanh.
- [ ] Ghi vào Comments: lệnh đã chạy, số test, chỗ nào phải sửa ngoài 8 bước.

## Nguồn

- Khảo sát `docs/products/elmich/surveys/2026-10-05-archive-port.md`: bảng 13 migration; danh sách "(b) phải chuyển" và "(c)
  sinh lại"; head `855ae928c3fa`.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 1, bước 1–8 và rủi ro.
- Trên `main`: `apps/api/src/dw_api/bootstrap/wiring.py:379-380` (seam tiền tố nghiêm).

## Comments

- 5/10/2026: lát port chạy trong phiên dựng đầu tiên của repo này, song song với lúc
  viết tài liệu. Trạng thái để `ready-for-agent` vì chưa có báo cáo xanh của agent
  port; agent port đặt `resolved` kèm commit khi các tiêu chí trên xanh.
- 5/10/2026, rà soát: thêm ngoài 8 bước một revision mới `bc3f0c1279fd` (FK của bốn
  bảng con và `delay_impact_analyses.supplier_update_id` mang `tenant_id`; Postgres
  kiểm FK không qua RLS) và `tests/integration/test_cross_tenant_writes.py` (tenant B
  gọi `SubmitSupplierUpdate`/`AnalyzeDelayImpact` với hồ sơ của A: `NotFoundError`,
  không gọi model, không dòng nào; database từ chối dòng con trỏ hồ sơ tenant khác).
  Integration của context từ đây là 9 file. Audit, trần chi tiêu: ticket 02; hạn giữ
  `follow_ups`: ticket 03.
