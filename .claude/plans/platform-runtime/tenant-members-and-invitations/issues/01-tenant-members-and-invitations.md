# 01 — Người dùng toàn tenant, sửa vai theo workspace, lời mời

Status: resolved (2026-10-08, nhánh `feat/platform-tickets`)
Blocked by: .claude/plans/platform-runtime/support-access/issues/01-support-grants-lifecycle.md
Area: platform-runtime

## Mục tiêu

Quản trị viên tổ chức thấy mọi người của tenant với vai ở từng workspace, sửa vai của một
người ở nhiều workspace trong một lần lưu, và mời một người chưa từng đăng nhập bằng email
(spec, Hiện trạng).

## Việc cần làm

1. **Danh sách toàn tenant.** `GET /api/v1/admin/members` (`platform.members.read`): mỗi
   người `user_id`, `display_name`, `email`, `status` (`invited` khi user chưa có dòng
   `platform.external_identities`, không thì `active`), `memberships`: [{`workspace_id`,
   `workspace_name`, `role_keys`}]. Đọc dưới RLS tenant của người gọi. Sắp theo tên với
   collation tiếng Việt nếu image PostgreSQL có (`SELECT collname FROM pg_collation WHERE
collname LIKE 'vi%'`; đo, ghi vào Comments), không thì theo `display_name` mặc định; có
   index phục vụ thứ tự đó, đứng đầu là `tenant_id`.
2. **Sửa vai nhiều workspace.** `PUT /api/v1/admin/members/{user_id}/memberships`
   (`platform.members.write`, `Idempotency-Key`). Body `memberships`: [{`workspace_id`,
   `role_keys`}]. Một giao dịch:
    - chỉ thay vai không quản trị (vai không mang scope `platform.*`); vai quản trị người đó
      đang giữ ở mỗi workspace giữ nguyên;
    - `role_keys` có vai quản trị mà người gọi không phải Platform Admin → 403
      (`_forbid_escalation`);
    - workspace có trong body mà chưa có membership → tạo; có membership mà không có trong
      body → bỏ vai không quản trị, còn vai quản trị thì giữ membership, không còn vai nào thì
      gỡ;
    - workspace ngoài tenant → 404; vai lạ → 404 kèm danh sách;
    - mỗi thay đổi một audit `platform.membership.grant` hoặc `.revoke`;
    - xóa cache AccessContext của từng workspace bị đổi (`membership_cache_pattern`).
3. **Lời mời.** `POST /api/v1/admin/invitations` (`platform.members.write`,
   `Idempotency-Key`): `display_name` (4–120), `email`, `memberships` (như bước 2, ít nhất một
   dòng có vai). Một giao dịch: email chuẩn hóa chữ thường; đã là thành viên tenant → 409
   `member_email_exists_in_tenant`; user ở `platform.support_staff` → 409
   `support_staff_not_member`; user chưa có → tạo `platform.users` (tên hiển thị, email),
   không tạo `external_identities`; rồi đặt membership như bước 2; audit
   `platform.member.invited` (`details`: `user_id`, workspace, vai; không email). Lần đăng
   nhập đầu, `identity_provisioning.py` nối danh tính vào user này theo email đã xác minh.
   Viết test chứng minh đường nối đó chạy với user tạo từ lời mời (failure-modes #4).
4. **Directory.** `GET /directory/members` thêm `status`, cùng hàm tính với bước 1 (một chủ).
5. **Chặn nhân viên hỗ trợ ở mọi đường đặt membership**: `PUT …/memberships` cũng trả 409
   `support_staff_not_member` (cùng kiểm với `GrantMembershipHandler`, `support-access`
   ticket 01).
6. Không gửi email. Docstring ghi: người quản trị báo người được mời ngoài sản phẩm; gửi email
   là P1.
7. `make generate-contracts`.

## Tiêu chí chấp nhận

Integration (`dw_platform/tests/integration`, `apps/api/tests`):

- [x] TM2: `GET /admin/members` của `org_admin` tenant A không chứa người chỉ thuộc tenant B;
      người không có `platform.members.read` → 403. `PUT …/memberships` với `user_id` hay
      `workspace_id` của tenant B → 404, không membership nào được tạo hay đổi.
- [x] TM1: người giữ `org_admin` và một vai thường ở ws1; `PUT` chỉ đổi vai thường →
      `org_admin` còn. `PUT` có `org_admin` do `org_admin` gọi → 403 và không membership nào
      đổi (cả giao dịch lùi). Gỡ phần giữ vai quản trị → test đầu đỏ.
- [x] `PUT` bỏ ws2 khỏi body → membership ws2 bị gỡ, có audit `.revoke`; cache của ws2 bị xóa
      (cache giả trung thực).
- [x] Lời mời: email mới → user tạo, `status=invited`, membership đúng; đăng nhập lần đầu bằng
      token dev có cùng email → bootstrap thấy workspace đó, `status=active`. Email đã là
      thành viên → 409.
- [x] TM3: email nhân viên hỗ trợ → 409 ở `POST /admin/invitations` và `PUT …/memberships`.
      Gỡ kiểm → test tương ứng đỏ.
- [x] TM4: hai lời mời đồng thời cùng email → một 201, một 409.
- [x] `Idempotency-Key` gửi lại cùng body → cùng kết quả, không audit thứ hai.
- [x] `make ci` xanh.

## Nguồn

- `apps/api/src/dw_api/routes/v1/admin_members.py:44`, `directory.py:38`;
  `packages/python/dw_platform/src/dw_platform/application/membership_admin.py:41-42, 123-159`;
  `adapters/persistence/identity_provisioning.py` (nối theo email);
  `application/cache.py:23`; `db/migrations/sql/0001_platform_baseline.sql:616`.
- `spec.md` của lát này: Kiểm soát TM1–TM4, câu hỏi còn mở 1.

## Comments

- 2026-10-08 (agent, Đạt giao quyết các điểm mở; quyết tạm):
    - **Collation tiếng Việt, đo:** image `postgres:16-alpine` (PostgreSQL 16.14, musl) có
      `vi-VN-x-icu` và `vi-x-icu` (`SELECT collname FROM pg_collation WHERE collname LIKE 'vi%'`).
      Với `An, Ánh, Ân, Bình, Dũng, Đạt, E, Ê, Zoe`: ICU xếp đúng `An | Ánh | Ân | Bình | Dũng | Đạt
| E | Ê | Zoe`; collation mặc định của database xếp mọi tên có dấu sau `Z` (`An | Bình | Dũng
| E | Zoe | Ánh | Ân | Ê | Đạt`). Dùng `COLLATE "vi-VN-x-icu"`, không có nhánh dự phòng: image
      khác thiếu collation thì truy vấn lỗi to thay vì xếp sai lặng lẽ, và
      `test_names_sort_the_vietnamese_way` đỏ.
    - **Index:** cột sắp (`users.display_name`) ở mặt phẳng danh tính, không có `tenant_id`, nên
      không có index nào "đứng đầu là `tenant_id`" mang được thứ tự đó. Danh sách không phân trang
      (một tenant), đọc qua `ix_memberships_tenant_user (tenant_id, user_id)` rồi sắp trong bộ
      nhớ của PostgreSQL; quy tắc index cho ORDER BY của `CLAUDE.md` nói về endpoint phân trang.
      Không thêm index.
    - **Trạng thái một chủ:** `member_status()` (`adapters/persistence/directory.py`), biểu thức
      SQL dùng chung cho `GET /directory/members` và `GET /admin/members`; `active` khi có một
      `external_identities` không phải liên kết chat (`CHANNEL_LINK_PROVIDERS`), không thì
      `invited`.
    - **Vai quản trị** = vai có scope `platform.*` (`is_administrative`, cùng chủ với
      `forbid_escalation`, tách khỏi `GrantMembershipHandler`). `PUT` tính vai cuối bằng
      `plan_memberships` (hàm thuần): vai xin + vai quản trị đang giữ; workspace không có trong
      body giữ vai quản trị, không còn gì thì gỡ. Platform Admin được thêm vai quản trị (giữ như
      `_forbid_escalation`).
    - **PUT trả** `{user_id, memberships}` (membership sau khi đổi; rỗng khi không còn), vì
      `Idempotency-Key` lưu một model.
    - **Lời mời:** `users.subject = 'invite:<uuid>'` (cột NOT NULL, UNIQUE), email chữ thường,
      không `external_identities`. Audit một `platform.member.invited` mỗi workspace (`details`:
      `user_id`, `roles`; không email). TM4 nhờ `INSERT ... ON CONFLICT (email) DO NOTHING`: lời
      mời thua chờ lời thắng commit rồi trả 409 `member_email_exists_in_tenant`.
    - **TM3** dùng trigger `platform.refuse_support_staff_membership()` của `support-access` 01
      (một kiểm cho mọi đường), dịch thành 409 `support_staff_not_member`. Thứ tự: email đã là
      thành viên tenant thì 409 `member_email_exists_in_tenant` trước.
    - Mã lỗi ở `details.reason_code` như `support-access` 01.
- Route: `GET /admin/members` → `[{user_id, display_name, email, status, memberships:
[{workspace_id, workspace_name, role_keys}]}]`; `PUT /admin/members/{user_id}/memberships` body
  `{memberships: [{workspace_id, role_keys}]}` → `{user_id, memberships}`; `POST
/admin/invitations` body `{display_name, email, memberships}` → 201 `{user_id, email,
display_name}`; `GET /directory/members` thêm `status`. Có trong contracts.
- Test: integration `test_tenant_members.py` (11, `dw_app`), API unit
  `test_tenant_members_endpoint.py` (3: xóa cache ws bị gỡ với cache giả trung thực, gửi lại
  `Idempotency-Key` cùng kết quả và một audit, trường lạ 422).
- Mutation (đều đỏ, đã khôi phục): bỏ giữ vai quản trị → TM1 đỏ; `forbid_escalation` không từ
  chối → TM1 đỏ; bỏ kiểm workspace ngoài tenant → TM2 đỏ; bỏ 404 người ngoài tenant → TM2 đỏ; bỏ
  dịch lỗi nhân viên hỗ trợ → TM3 đỏ; bỏ `ON CONFLICT` → TM4 đỏ; trạng thái luôn `active` → ca
  lời mời đỏ; bỏ collation → ca thứ tự đỏ; bỏ xóa cache ở `PUT` → ca cache đỏ.
- `reviewing-feature-security`: (1) tenant: mọi đọc ghi qua `tenant_session` và lọc lại
  `tenant_id`; `users` chỉ đọc qua membership của tenant, trừ tra email của lời mời, chỉ trả lời
  "đã là thành viên ở đây" (409); test âm TM2. Cache: xóa theo `membership_cache_pattern` từng
  workspace đổi. (2) authz: scope ở service; leo thang chặn ở service, SoD và nhân viên hỗ trợ
  chặn ở database. (3) không chạm agent. (4) `display_name` là chữ tự do, chỉ hiển thị. (5) audit
  cùng giao dịch, một sự kiện mỗi thay đổi. (6) lời mời không có hạn và không gửi email (P1); người
  được mời chưa đăng nhập không có quyền gì cho tới khi IdP xác minh email.
- `reviewing-deployment-security`: không bề mặt dev, không secret, không header mới, không URL ra
  ngoài, không image. 404 cho người hay workspace ngoài tenant như không tồn tại.
- Còn mở (spec câu hỏi 1): nối theo email khi broker IdP của khách; lời mời giữ chỗ cho bất kỳ ai
  có email đó được IdP xác minh.
