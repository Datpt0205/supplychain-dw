# 01 — Quyền hỗ trợ: bảng, vòng đời, route phía khách và đội hỗ trợ

Status: resolved (2026-10-08, nhánh `feat/platform-tickets`; bước 7, console `/platform` ở web,
do phiên web làm, route và contract đã có)
Blocked by: —
Area: platform-runtime

## Mục tiêu

Khách nhờ, đội vận hành chọn người, quyền có hạn và thu hồi được. Ticket này dựng dữ liệu
và vòng đời của quyền hỗ trợ, phía khách (yêu cầu, cấp, duyệt, từ chối, thu hồi, xem) và
phía đội vận hành (danh sách nhân viên hỗ trợ, giao người). Dựng ngữ cảnh truy cập từ quyền
là ticket 02.

## Việc cần làm

1. **ADR.** Viết ADR trung tính "Customer-granted support access" ở `docs/adr/` (lý do nền
   tảng, không dẫn tài liệu sản phẩm), status Accepted khi Đạt duyệt, cùng thay đổi với
   code. Chọn số và tên tệp chưa dùng ở `main` lẫn ở nhánh nào đang merge từ `main`, để
   merge không đè một tệp đang có.
2. **Migration** theo mục Dữ liệu của `spec.md`:
    - `platform.support_staff`; `dw_app` SELECT; vai provisioner SELECT, INSERT, DELETE.
    - `platform.support_grants`: cột, CHECK theo trạng thái (mỗi nhóm cột có khi và chỉ
      khi đúng trạng thái), RLS tenant FORCE, index, FK có `ON DELETE` và index; grant như
      spec.
    - `UPDATE platform.roles SET scopes = scopes || '["support.request"]' WHERE key =
'org_admin'` (có downgrade).
    - Cờ `support_access`: không thêm vào gói nào.
    - Dòng trong `test_privileges.py`; `test_rls_coverage.py` thấy bảng mới.
3. **Application** `dw_platform/application/support_access.py`:
    - `SupportScopeCatalog`: `register(key, label, scopes, resource_types)`; từ chối bộ có
      scope bắt đầu bằng `approvals.`, `runs.`, `audit.`, `knowledge.`, `memory.`,
      `platform.`, `support.`, `directory.`; từ chối khóa trùng; đóng băng sau khi composition
      root dựng xong.
    - `SupportResourcePort` (Protocol, consumer là nền tảng): `describe(context,
resource_type, resource_id) -> str | None`; `None` thì tài nguyên không có trong
      workspace (404). `resource_type = 'workspace'` do nền tảng tự trả nhãn.
    - `SupportGrantService`: `Request`, `Approve`, `Reject`, `Revoke`, `List`. Có
      `support.grant`: kiểm `scopes(bộ) ⊆ scopes của người cấp trong workspace đó`, ghi
      `pending_assignment`, đóng dấu `scopes`, `granted_by`. Chỉ `support.request`: ghi
      `pending_approval`. Duyệt kiểm tập con theo người duyệt. Từ chối bắt lý do. Thu hồi mọi
      trạng thái chưa kết thúc.
    - Một hàm `grant_effective_state(grant, now, grantor_scopes)` trả `active`, `expired`,
      `ineffective` (người cấp không còn `support.grant` hoặc một scope đã đóng dấu) hay
      trạng thái lưu. `List` dùng nó; ticket 02 dùng đúng hàm này.
    - Audit cùng giao dịch: `support.grant.requested`, `.approved`, `.rejected`, `.revoked`;
      `details` gồm `support_grant_id`, `code`, `scope_set_key`, `resource_type`; không lý do.
4. **Route** `apps/api/src/dw_api/routes/v1/support.py` theo bảng API của `spec.md`.
   `RequireIdempotency` trên mọi POST. Body `extra="forbid"`. RLS của bảng chỉ lọc tenant,
   nên repository lọc thêm `workspace_id` của người gọi (lấy từ `AccessContext`) ở mọi đọc
   và mọi lệnh theo id; quyền của workspace khác là 404. `support.request` chỉ thấy yêu cầu
   do chính mình gửi. Tenant chưa có cờ `support_access`: `GET /support/catalog`, `GET` và `POST
/support/grants` trả 403 `support_access_not_enabled` (web đọc mã này để khóa nút kèm lý do).
5. **Phía đội vận hành** (ProvisioningContext, Platform Operator): `GET/POST/DELETE
/platform/support-staff`; `GET /platform/support-requests` (mọi tenant, chỉ
   `pending_assignment`: mã, tên công ty, workspace, nhãn phạm vi, nhãn chế độ, thời hạn
   xin, lý do, lúc gửi); `POST /platform/support-requests/{id}/assign` (ghi `staff_user_id`,
   `assigned_by`, `activated_at`, `expires_at`; audit provisioning và
   `support.grant.assigned` vào tenant). Không kiểm xung đột lợi ích (spec, câu hỏi còn mở
   2): docstring ghi rõ.
6. **Membership từ chối nhân viên hỗ trợ**: `GrantMembershipHandler` trả 409
   `support_staff_not_member` khi user ở `support_staff`. Đường đặt membership mới của
   `tenant-members-and-invitations` dùng cùng kiểm.
7. **Console** `/platform` (khung hiện có, operator only): bảng "Support requests" (giao
   người bằng `Select` chỉ gồm danh tính trong `support_staff`) và "Support staff" (thêm theo
   email, gỡ). Không cột nội dung hay số liệu nghiệp vụ.
8. Wiring ở `apps/api/src/dw_api/bootstrap/wiring.py`; `make generate-contracts`. Catalog
   rỗng trên `main`: `GET /support/catalog` trả `[]`, `POST /support/grants` trả 422
   `support_scope_set_unknown`.

## Tiêu chí chấp nhận

Unit:

- [x] `SupportScopeCatalog` từ chối bộ có `approvals.decide`, `runs.read`, `platform.admin`;
      từ chối khóa trùng; đăng ký sau khi đóng băng → lỗi. Gỡ danh sách tiền tố cấm → test đỏ.
- [x] `grant_effective_state`: `active` trước `expires_at`, `expired` đúng tại `expires_at`;
      `ineffective` khi người cấp mất `support.grant` hoặc mất một scope đã đóng dấu.

Integration (`dw_platform/tests/integration`, PostgreSQL compose; catalog có một bộ test
đăng ký trong fixture):

- [x] SA1: vai không có `support.*` gọi `POST /support/grants` → 403; `org_admin` → dòng
      `pending_approval`; không đường nào đưa dòng đó sang `active` khi chưa duyệt.
- [x] SA2: người cấp thiếu một scope của bộ → 403 `support_scope_not_held`, không dòng nào
      được ghi. Gỡ kiểm tập con → test đỏ.
- [x] SA3: body có `staff_user_id` → 422; giao cho user không ở `support_staff` → 409; giao
      cho quyền đã `active` hoặc `rejected` → 409.
- [x] SA9: `POST /admin/members` với email nhân viên hỗ trợ → 409 `support_staff_not_member`.
      Gỡ kiểm → test đỏ.
- [x] SA11: tenant không có cờ → `GET /support/catalog` và `POST /support/grants` 403
      `support_access_not_enabled`; bật qua `feature_overrides` → 201.
- [x] SA13: người tenant B, và người giữ `support.grant` ở ws2 của tenant A, đọc, duyệt, từ
      chối, thu hồi quyền của ws1 tenant A theo id → 404, dòng không đổi, không audit mới;
      `GET /support/grants` của họ không có quyền đó; kết nối không gắn tenant đọc 0 dòng.
- [x] CHECK: chèn `status='active'` thiếu `expires_at` → check violation;
      `duration_hours=337` → check violation; `dw_app` DELETE → permission denied.
- [x] Audit: mỗi lệnh đúng một sự kiện, có `support_grant_id`, không có `reason`.
- [x] `make ci` xanh.

## Nguồn

- `packages/python/dw_platform/src/dw_platform/application/membership_admin.py` (chống leo
  thang, audit cùng giao dịch); `application/provisioning.py` (khuôn operator);
  `db/migrations/sql/0001_platform_baseline.sql:340` (`platform_operators`), `:213`
  (`feature_overrides`); `application/authorization.py:47-49`.
- `spec.md` của lát này: Dữ liệu, API, SA1–SA3, SA9, SA11, SA13.

## Comments

- 2026-10-08 (agent, Đạt giao quyết các điểm mở; quyết tạm, ghi ở ADR 0024 "Provisional calls"):
    - **ADR** `docs/adr/0024-customer-granted-support-access.md`, trung tính, status "Proposed
      (provisional, …)". Số 0024 vì nhánh sản phẩm merge từ `main` đã dùng tới 0023.
    - **Migration** `af8ee878b4ab` (sau `983b509c3f0f`, một head). `support_staff`,
      `support_grants` (RLS FORCE, policy `tenant_isolation_support_grants`), CHECK "có khi và chỉ
      khi" đặt trên cột thời điểm của từng bước, vì các cột `*_by` là `ON DELETE SET NULL`;
      `staff_user_id` là `ON DELETE RESTRICT`. Trigger `platform.guard_support_grant()` giữ máy
      trạng thái cho mọi người ghi (kể cả migrator): chèn chỉ ở `pending_approval` hay
      `pending_assignment`, chỉ đi tới (duyệt, từ chối, giao, thu hồi), điều đã quyết không đổi;
      trigger cũng sinh `code` theo tenant dưới advisory lock. Trigger
      `platform.refuse_support_staff_membership()` trên `memberships` (INSERT, UPDATE của
      `user_id`, `role_keys`, `permission_set_keys`) là kiểm SA9 duy nhất, nên mọi đường đặt
      membership (org admin, lời mời, `assign_org_admin` của operator) cùng bị chặn; adapter
      dịch ràng buộc `ck_memberships_not_support_staff` thành 409. Grant: `dw_app` SELECT, INSERT
      cột phía khách, UPDATE cột của duyệt, từ chối, thu hồi, không DELETE; provisioner SELECT,
      INSERT, DELETE `support_staff`, SELECT và UPDATE cột giao người trên `support_grants`,
      INSERT `audit_events` (audit `support.grant.assigned` vào tenant cùng giao dịch).
      `org_admin` thêm `support.request`.
    - **Mã lỗi:** giữ `code` của nền tảng (`permission_denied` 403, `conflict` 409,
      `validation_failed` 422), mã riêng ở `details.reason_code` (`SupportRefusal`):
      `support_access_not_enabled`, `support_scope_not_held`, `support_scope_set_unknown`,
      `support_resource_type_not_offered`, `support_grant_wrong_status`, `support_staff_required`,
      `support_staff_not_member`. Lý do: `ErrorCode` là taxonomy công khai có bản TypeScript; không
      thêm một mã cho mỗi tính năng.
    - **`support.*` đọc đúng chữ** trong scope của người gọi, không vai nào đứng thay
      (`platform_admin` cũng không): hiệu lực của quyền về sau tính từ scope membership của
      người cấp, nơi không có ngoại lệ vai, nên một quyền cấp nhờ vai sẽ "ineffective" ngay.
    - **Từ chối, thu hồi không đòi cờ** `support_access` (tắt tính năng không được khóa tay khách
      khi muốn đóng); catalog, danh sách, tạo, duyệt thì đòi.
    - **Bỏ khóa FOR SHARE** khi giao người: provisioner không có UPDATE trên `support_staff`; ca
      tranh chấp vô hại vì ngữ cảnh hỗ trợ (ticket 02) đòi người còn trong danh sách mỗi request.
- Route: `GET /support/catalog`, `GET|POST /support/grants`, `POST /support/grants/{id}/approve`,
  `/reject` (body `reason`), `/revoke`; `GET|POST /platform/support-staff`, `DELETE
/platform/support-staff/{user_id}`, `GET /platform/support-requests`, `POST
/platform/support-requests/{grant_id}/assign` (body `staff_user_id`). Có trong
  `contracts/openapi/openapi.json` và `packages/typescript/api-client`.
- Test: unit `dw_platform/tests/unit/test_support_access.py` (17), integration
  `test_support_grants.py` (14, `dw_app` và `dw_provisioner`), `test_privileges.py::
test_support_access_privileges`, API unit `test_support_endpoint.py` (5) và
  `test_platform_endpoint.py::test_every_platform_route_refuses_a_non_operator` (duyệt mọi route
  `/platform` từ `app.routes`).
- Mutation (đều đỏ, đã khôi phục): `_side` cho mọi người là requester → SA1 đỏ; máy trạng thái
  cho `pending_approval → active` → SA1 đỏ; bỏ kiểm tập con → SA2 đỏ; `grant_effective_state`
  bỏ qua người cấp → ca ineffective đỏ; bỏ kiểm `support_staff` khi giao → SA3 đỏ; trigger
  membership không bao giờ từ chối → SA9 đỏ; bỏ kiểm cờ → SA11 đỏ; bỏ lọc workspace trong
  repository → SA13 đỏ; thêm `reason` vào `details` audit → ca audit đỏ; danh sách tiền tố cấm
  rỗng → 5 ca unit đỏ.
- Còn lại: bước 7 (bảng "Support requests", "Support staff" ở `/platform`) là việc của phiên web.
- `reviewing-feature-security` (2026-10-08): (1) tenant: `support_grants` RLS ENABLE + FORCE,
  `tenant_session` gắn tenant mỗi giao dịch, repository lọc thêm workspace; test âm SA13 (tenant
  B, ws2 → 404, danh sách không có, kết nối không gắn tenant đọc 0 dòng). Không cache, không
  object path. (2) authz: quyết ở service ngay chỗ ghi, máy trạng thái ở database cho mọi người
  ghi; danh tính từ `AccessContext` đã xác minh; scope đóng dấu trên dòng, người đọc sau dùng bản
  đóng dấu. Cache AccessContext cũ (trước migration) thiếu `support.request` → hạn chế hơn, không
  rộng hơn. (3) autonomy: không chạm đường agent. (4) nội dung không tin cậy: `reason` là chữ tự
  do, chỉ lưu và hiển thị, không vào prompt, không vào audit. (5) audit cùng giao dịch ở mọi lệnh,
  kể cả giao người (update + `audit_events` + `provisioning_audit` một giao dịch). (6) vòng đời:
  quyền không bị xóa (giữ cùng audit, xóa theo tenant bằng CASCADE); hết hạn là trạng thái suy
  ra; cờ tắt là mặc định từ chối.
- `reviewing-deployment-security`: (1) không có bề mặt dev/mock mới; route đi qua xác thực như
  route khác. (2) quyền của tenant hay workspace khác trả 404 như quyền không tồn tại; lỗi giữ
  taxonomy. (3) không có secret mới. (4) không có header mới ở ticket này (header
  `X-DW-Support-Grant` là ticket 02, phải vào `_CORS_HEADERS`). (5) không có URL ra ngoài.
  (6) không có image hay dependency mới.
- 8/10/2026, bước 7 (console `/platform`, nhánh `feat/antd-everywhere`): hai thẻ
  antd "Yêu cầu hỗ trợ chờ giao người" (mã, khách và workspace, phạm vi, thời
  hạn, lý do, giờ gửi giờ Việt Nam, `Select` chỉ gồm nhân viên trong
  `support_staff` rồi "Giao") và "Nhân viên hỗ trợ" (thêm theo email kèm ghi
  chú, gỡ có hộp xác nhận). Không cột nội dung hay số liệu nghiệp vụ. Client
  `listSupportStaff`, `addSupportStaff`, `removeSupportStaff`,
  `listSupportRequests`, `assignSupportRequest`, mỗi kiểu có dòng `SameType`
  so với `generated/platform.d.ts`. Vitest `support-console.test.tsx`: nút
  "Giao" khóa tới khi chọn người, chỉ một lựa chọn (đội hỗ trợ), gọi assign
  đúng id.
