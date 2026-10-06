# Quyết định approval có vết audit; approval, run và audit đọc theo workspace

Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Hai lỗ trong đường approval của nền tảng mà một
bounded context dùng approval làm cổng duyệt (run dừng ở `interrupt`, người quyết qua
route chung, run chạy tiếp) sẽ gặp ngay ở cổng đầu tiên. Cả hai đã kiểm trên code của
`main` ngày 3/10/2026.

## Mục tiêu

1. Mọi quyết định approval để lại một dòng trong `platform.audit_events`, cùng giao dịch
   với dòng quyết định, người làm là người quyết (ticket 01).
2. Approval, run và audit chỉ đọc được trong workspace của người gọi, và route nào cũng
   kiểm scope của nó (ticket 02).

## Hiện trạng (đã kiểm trong code)

- `ApproveAndResumeService.decide`
  (`packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py:96-182`) ghi
  `approval_requests` và `approval_decisions` rồi commit; không ghi `audit_events`.
  `add_decision` (`packages/python/dw_platform/src/dw_platform/adapters/persistence/repositories.py:147-159`)
  chỉ chèn `approval_decisions`.
- Dòng audit duy nhất quanh lần quyết là `run.resumed` của runner
  (`adapters/langgraph_runner.py:583`), và người làm của nó là người khởi chạy run
  (`approval_flow.py:152`, `actor_id=record.requested_by`), không phải người quyết.
- `dw_app` giữ UPDATE, DELETE trên `platform.approval_decisions`:
  `db/migrations/sql/0001_platform_grants.sql:31-37` cấp mọi quyền, chỉ thu UPDATE,
  DELETE của `audit_events`. Không code nào UPDATE `approval_decisions`. DELETE thì lane
  offboarding cần: nó xóa mọi bảng mà `dw_app` có DELETE
  (`adapters/persistence/offboarding.py:90-98`).
- RLS của `approval_requests`, `audit_events`, `worker_runs` chỉ lọc tenant
  (`db/migrations/sql/0001_platform_baseline.sql:839, 842, 875`).
- `GET /approvals` (`apps/api/src/dw_api/routes/v1/approvals.py:44-76`) chỉ cần
  `approvals.read` (vai `member` có), trả `payload` của approval mọi workspace trong
  tenant; `list_pending` không lọc workspace. `GET /approvals/{id}` và `decide` đọc approval
  theo id, cũng không lọc workspace, dù chú thích ở `approval_flow.py:118-119` ghi
  "tenant/workspace-scoped by RLS". Khi run chạy tiếp, `RunContext` lấy `workspace_id`
  của người quyết (`approval_flow.py:151`), không của run; `RunRecord` không mang
  `workspace_id` dù `worker_runs` có cột đó (`run_store.py:70-103, 354`).
- `GET /runs/{run_id}` (`apps/api/src/dw_api/routes/v1/runs.py:41-58`) không gọi
  `authorization.require`, trả `result`; `run_store.get` chỉ gắn tenant.
- `GET /audit/events` (`apps/api/src/dw_api/routes/v1/audit.py:39-72`) kiểm
  `approvals.read`, trả audit cả tenant kèm `details`. Scope `audit.events` có trong
  catalogue (`db/migrations/sql/0001_platform_reference.sql:68, 74`, vai `director`,
  `executive`) mà không route nào đọc.

## Trong phạm vi

- Ticket 01: sự kiện audit `approval.decided` cùng giao dịch với quyết định; thu UPDATE
  của `dw_app` trên `approval_decisions`.
- Ticket 02: lọc approval, run, audit theo workspace của người gọi; `GET /runs/{id}` kiểm
  `runs.read`; route audit kiểm `audit.events`; run chạy tiếp với workspace của run.

## Ngoài phạm vi

- RLS theo workspace cho bảng `platform` (đổi policy): ticket 02 lọc ở repository và route
  trước, có test âm; đổi policy là một quyết định riêng.
- Màn audit cho cả tenant (một vai xem mọi workspace): chưa ai cần (Câu hỏi còn mở 1).
- Miễn tách nhiệm theo tenant (`sod_waivers`) và chế độ nghiêm của approval: giữ nguyên.

## Tiêu chí xong

- Hai ticket `resolved`; `make ci` xanh; integration của `dw_platform`,
  `dw_agent_runtime`, `apps/api` xanh.
- Mỗi kiểm soát mới có một test âm đã thấy đỏ khi gỡ kiểm soát (ghi trong Comments của
  ticket).

## Phụ thuộc

Không bị chặn bởi gì. Context đầu tiên dùng approval làm cổng duyệt chờ ticket 01 trước
cổng đầu tiên của nó. Ticket 02 không chặn bản demo của context đó, vì payload cổng của nó
chỉ mang mã định danh; ticket 02 cần trước khi một tenant có nhiều workspace dùng thật.

## Câu hỏi còn mở

1. **Audit cho cả tenant.** Có vai nào (vd quản trị tổ chức) cần xem audit của mọi workspace
   không? Nếu có, thêm một scope riêng và một route riêng, không nới route hiện có.

## Danh sách ticket

| #   | Ticket                                                                                                                     | Status          | Blocked by |
| --- | -------------------------------------------------------------------------------------------------------------------------- | --------------- | ---------- |
| 01  | [Quyết định approval ghi `audit_events` cùng giao dịch](issues/01-approval-decision-writes-audit.md)                       | ready-for-agent | —          |
| 02  | [Approval, run và audit đọc theo workspace; route nào cũng kiểm scope](issues/02-approval-run-audit-reads-by-workspace.md) | resolved        | —          |
