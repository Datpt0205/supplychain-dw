# 03 — Hạn giữ cho `supply_chain.follow_ups`

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

`follow_ups` lớn không giới hạn (failure-modes #6): mỗi hồ sơ mỗi đợt nhắc thêm một dòng,
`dw_app` không có DELETE (`dc2285c629d4_supply_chain_follow_ups.py:115`), không lane
dọn nào đọc bảng này; dòng chỉ mất khi hồ sơ PO bị xóa hoặc offboarding xóa `po_cases`
và cascade theo.

## Việc cần làm

1. Migration mới (revision ngẫu nhiên của alembic): hàm `SECURITY DEFINER`
   `supply_chain.prune_follow_ups(older_than interval)` xóa dòng đã đóng
   (`status IN ('done','resolved')`) có `closed_at` cũ hơn hạn; không bao giờ xóa dòng
   `open`. `GRANT EXECUTE` cho `dw_app`, không grant DELETE trên bảng.
2. Hạn giữ đọc từ policy `supply_chain_follow_ups` (một chủ của số, tenant đổi được
   qua `PolicyOverridePort`, có sàn nền tảng không hạ được); mặc định 180 ngày.
3. Gọi hàm từ lane retention có sẵn của worker (đăng ký ở composition root, không thêm
   nhánh vào vòng lặp lane).

## Tiêu chí chấp nhận

- [x] Integration: dòng đã đóng quá hạn bị xóa; dòng `open` mọi tuổi và dòng đã đóng
      trong hạn còn nguyên; tenant khác không bị đụng.
- [x] `test_privileges.py` vẫn xanh: `dw_app` không có DELETE/TRUNCATE trên `follow_ups`.
- [x] Mutation: bỏ điều kiện `status <> 'open'` thì test đỏ (ghi vào Comments).

## Nguồn

- `.claude/rules/failure-modes.md` #6; mẫu `platform.prune_notifications()`.

## Comments

**2026-10-07 (agent, Đạt giao quyết tạm):**

- **Hàm.** `992e7c6ae4fe`: `supply_chain.prune_follow_ups(older_than interval)`,
  SECURITY DEFINER, `REVOKE ALL FROM PUBLIC`, `GRANT EXECUTE TO dw_app`; `dw_app`
  vẫn không có DELETE/TRUNCATE trên bảng. Vì chạy dưới quyền definer (không bị RLS
  thu hẹp), hàm tự thu hẹp theo `app.tenant_id` VÀ `app.workspace_id` của
  transaction gọi; chưa bind thì `= NULL` không khớp dòng nào. Xóa
  `status <> 'open' AND closed_at < now() - older_than`. Hạn qua ranh giới quyền
  nên hàm tự chặn dưới 1 ngày (SQLSTATE 22023). Thêm index một phần
  `ix_follow_ups_tenant_id_workspace_id_closed_at WHERE status <> 'open'`.
- **Hạn ở policy (quyết tạm).** `supply_chain_follow_ups@1.2.0` (schema 1.2) thêm
  `closed_retention_days: 180`; 1.2 bắt buộc có, 1.0/1.1 không được có (override
  cũ vẫn hợp lệ, dùng số nền tảng). Số của tài liệu nền tảng là mặc định VÀ sàn:
  `follow_up_retention_days` = max(của tenant, của nền tảng); tenant chỉ giữ lâu
  hơn được. `SetFollowUpPolicyOverride` từ chối số ngắn hơn sàn (DomainError, có
  `minimum`), để không lưu một hạn không ai áp. Giới hạn 1–3650 ngày ở schema.
  Không có trần trên riêng cho tenant (ngoài 10 năm): giữ lâu hơn chỉ tốn chỗ của
  chính tenant.
- **Lane.** `PruneClosedFollowUps` (`application/follow_up_retention.py`) chạy
  theo từng workspace có hồ sơ (`workspaces_with_cases()`, như lượt quét), dưới
  `sweep_context` (không role, không scope), resolve policy của tenant, gọi hàm;
  một workspace lỗi được log, các workspace khác vẫn được dọn. Đăng ký ở composition
  root của worker: `supply_chain_follow_ups_retention`, nhịp retention (1 giờ),
  qua `build_retention_consumer`; không thêm nhánh vào vòng lặp lane.
- **Mutation (migration chạy lại mỗi lần):** bỏ điều kiện tenant →
  `test_a_prune_bound_to_one_tenant_cannot_reach_another` đỏ (test để hai tenant
  chung một workspace id, để chỉ còn điều kiện tenant chặn); bỏ điều kiện workspace →
  `..._leaves_the_tenants_other_workspaces` đỏ; bỏ chặn 1 ngày → ba case
  `test_a_term_under_a_day_is_refused...` đỏ; `max` → hạn của tenant →
  `shorter-is-the-floor` đỏ; bỏ kiểm sàn ở `SetFollowUpPolicyOverride` →
  `test_an_override_shorter_than_the_platform_term_is_refused` đỏ.
  **Bỏ riêng `status <> 'open'` thì test vẫn xanh**, và không test nào làm nó đỏ
  được: `ck_follow_ups_closed` buộc dòng `open` có `closed_at IS NULL`, nên
  `closed_at < ...` không bao giờ khớp dòng open. Điều kiện vẫn giữ: nó nói rõ ý
  định, và planner cần nó để dùng index một phần. Mutation thay bằng
  `COALESCE(closed_at, opened_at)` và bỏ status →
  `test_closed_follow_ups_past_the_term_go_and_nothing_else_does` đỏ (dòng open
  3000 ngày bị xóa). Guard "chưa bind thì RETURN 0" đã bỏ vì không thể đỏ
  (failure-modes #3); hành vi do `= NULL` đảm nhận và có test.
- **reviewing-feature-security.** (1) Tenant: hàm definer tự thu hẹp tenant +
  workspace; test cùng workspace id khác tenant, khác workspace cùng tenant, và
  không bind. (2) Authz: chỉ `dw_app` EXECUTE (test catalog); PUBLIC không;
  ghi đè policy vẫn cần `follow_up_policy.write`, thêm sàn. (3) Autonomy: không
  liên quan (không có agent/tool). (4) Nội dung không tin cậy: không có; số ngày
  là int đã validate, còn bị hàm chặn lại. (5) Audit: xóa theo hạn là dọn dẹp hệ
  thống, không audit từng dòng (như `prune_notifications`); lần đóng đã được audit
  khi người đóng (`supply_chain.follow_up.done`). (6) Vòng đời: đây chính là phía
  "hủy" còn thiếu; lỗi một workspace không chặn workspace khác; dòng `open` không
  bao giờ bị xóa.
