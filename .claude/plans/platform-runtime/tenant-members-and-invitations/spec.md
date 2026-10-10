# Người dùng toàn tenant, sửa vai theo workspace, lời mời

Status: ready-for-agent
Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Quản trị viên tổ chức cần thấy mọi người của tenant
kèm vai ở từng workspace, sửa vai của một người ở nhiều workspace trong một lần lưu, và mời
một người chưa từng đăng nhập bằng email. Một context có màn quản trị người dùng của riêng
nó dựng trên các route này; nền tảng không biết vai nào là của context nào.

## Hiện trạng (đã kiểm trong code ngày 3/10/2026)

- `GET /directory/members` (`apps/api/src/dw_api/routes/v1/directory.py:38`) chỉ liệt kê
  người của workspace hiện tại, kiểm `directory.read`.
- `POST /admin/members` (`apps/api/src/dw_api/routes/v1/admin_members.py:44`) là upsert từng
  membership; `GrantMembershipHandler`
  (`packages/python/dw_platform/src/dw_platform/application/membership_admin.py:123-157`)
  từ chối người chưa đăng nhập: "no user with that email has signed in yet" (`:139`).
  `_forbid_escalation` (`:159`) chặn cấp vai quản trị cho người gọi không phải Platform Admin.
  Audit `platform.membership.grant`, `.revoke` (`:41-42`).
- `identity_provisioning.py` (`adapters/persistence/`, docstring đầu tệp) nối danh tính đã
  xác minh vào user có sẵn theo `(issuer, subject)`, rồi `subject`, rồi email; bước email dựa
  vào IdP xác minh email.
- `platform.users.email` có `UNIQUE (email)` phân biệt hoa thường
  (`db/migrations/sql/0001_platform_baseline.sql:616`).
- Xóa cache AccessContext theo `membership_cache_pattern(tenant_id, workspace_id)`
  (`application/cache.py:23`).

## Mục tiêu

1. `GET /admin/members`: mọi người của tenant, trạng thái `invited`/`active`, membership
   theo workspace kèm vai.
2. `PUT /admin/members/{user_id}/memberships`: thay vai không quản trị ở nhiều workspace trong
   một giao dịch; vai quản trị giữ nguyên.
3. `POST /admin/invitations`: tạo sẵn user theo email rồi đặt membership; lần đăng nhập đầu
   nối danh tính vào user đó.
4. `GET /directory/members` thêm `status`, cùng hàm tính.

## Ngoài phạm vi

| Việc                                | Vì sao                                                                      |
| ----------------------------------- | --------------------------------------------------------------------------- |
| Gửi email lời mời                   | Nền tảng không có kênh email; người quản trị báo người được mời; P1         |
| Khóa, mở khóa tài khoản theo tenant | Cần trạng thái treo membership; khóa ở IdP khóa mọi tenant của người đó; P1 |
| Màn quản trị người dùng mới ở web   | Trang `/admin` hiện có giữ nguyên; context dựng màn của nó trên các route   |

## Kiểm soát

| #   | Kiểm soát                                        | Test âm                                                                                    |
| --- | ------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| TM1 | Sửa vai giữ vai quản trị; không cấp vai quản trị | `PUT` có `org_admin` do `org_admin` gọi → 403, không membership nào đổi                    |
| TM2 | Chỉ người của tenant mình                        | `GET /admin/members` của tenant A không có người chỉ thuộc tenant B; workspace của B → 404 |
| TM3 | Nhân viên hỗ trợ không thành member              | Email nhân viên hỗ trợ ở `POST /admin/invitations`, `PUT …/memberships` → 409              |
| TM4 | Một email một user                               | Hai lời mời đồng thời cùng email → một 201, một 409                                        |

TM3 dùng bảng `platform.support_staff` của `support-access` ticket 01.

## Tiêu chí xong

- Ticket 01 `resolved`; `make ci` xanh; integration của `dw_platform`, `apps/api` xanh.
- Mỗi kiểm soát có test âm đã thấy đỏ khi gỡ kiểm soát (Comments).

## Phụ thuộc

`.claude/plans/platform-runtime/support-access/issues/01-support-grants-lifecycle.md`
(bảng `support_staff` cho TM3).

## Câu hỏi còn mở

1. **Nối theo email khi broker IdP.** Lời mời dựa vào đường nối theo email sẵn có. Trước khi
   bật SSO cho khách qua broker, đường đó phải đóng hoặc đòi email đã xác minh bởi IdP của
   chính tenant.

## Danh sách ticket

| #   | Ticket                                                                                                 | Status   | Blocked by                                                                          |
| --- | ------------------------------------------------------------------------------------------------------ | -------- | ----------------------------------------------------------------------------------- |
| 01  | [Người dùng toàn tenant, sửa vai theo workspace, lời mời](issues/01-tenant-members-and-invitations.md) | resolved | .claude/plans/platform-runtime/support-access/issues/01-support-grants-lifecycle.md |
