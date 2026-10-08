# Quyền hỗ trợ do khách cấp: có hạn, theo phạm vi, khách xem được nhật ký

Status: ready-for-agent
Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Khi có khách thật, đội vận hành phải xem dữ liệu
của khách: máy đọc sai một trang, khách nhờ nhập liệu lúc bắt đầu dùng. Hôm nay chỉ có
hai đường và cả hai đều hỏng: tự thêm mình làm `platform_admin` (vai này vượt mọi scope,
không có hạn, khách không biết), hoặc xin mật khẩu của khách. Lát này dựng đường thứ ba:
khách cấp một quyền có phạm vi, có hạn, thu hồi được; đội vận hành chọn người; nhân viên
dùng danh tính của chính mình, có xác thực hai lớp, chỉ mang scope đóng dấu trong quyền;
mọi truy cập có audit khách đọc được.

Nền tảng không biết tài nguyên của context là gì. Context đăng ký bộ scope được phép cấp
và nhãn tài nguyên ở composition root; thu hẹp theo tài nguyên của context là việc của
context đó.

## Hiện trạng (đã kiểm trong code ngày 3/10/2026)

- `AuthorizationService.is_allowed`
  (`packages/python/dw_platform/src/dw_platform/application/authorization.py:47-49`)
  trả `True` cho mọi hành động khi `admin_role` có trong `context.roles`.
- Bảng `platform.platform_operators` (`db/migrations/sql/0001_platform_baseline.sql:340`)
  là mặt phẳng danh tính không tenant; chưa có bảng nào cho nhân viên hỗ trợ hay quyền hỗ
  trợ.
- `VerifiedIdentity` (`packages/python/dw_platform/src/dw_platform/application/ports.py:26`)
  không mang `amr`, `acr`; `infra/keycloak/dw-realm.json` không có OTP hay mapper nào cho hai
  claim đó.
- `RequireAccessContext` (`apps/api/src/dw_api/dependencies/auth.py:67`) dựng ngữ cảnh từ
  membership, qua `CachingMembershipLookup`
  (`packages/python/dw_platform/src/dw_platform/adapters/persistence/caching_lookup.py:100`).
  Cache là fail-open (`application/cache.py`).
- `GET /runs/{run_id}` (`apps/api/src/dw_api/routes/v1/runs.py:41-58`) không kiểm scope
  nào: thiếu scope chỉ chặn được route có kiểm scope (ticket 02 của
  `approval-audit-and-workspace` sửa route đó; lát này không chờ nó vì mặc định từ chối).
- `GET /auth/bootstrap` trả `is_platform_operator` (`apps/api/src/dw_api/routes/v1/auth.py:38`),
  chưa có cờ cho nhân viên hỗ trợ.
- `entitlements.feature_overrides` là `jsonb DEFAULT '[]'`
  (`db/migrations/sql/0001_platform_baseline.sql:213`): chỗ bật một tính năng cho một
  tenant mà không đổi gói.
- Web: `NavItem` có `operatorOnly` (`apps/web/lib/nav/types.ts`); API client gửi
  `X-Tenant-Id`, `X-Workspace-Id` từ `apps/web/lib/session.ts:146-147`; registry theo tiền tố
  ở `apps/web/lib/approvals/registry.ts` là khuôn cho một registry do context cắm vào.

## Mục tiêu

1. Khách yêu cầu, cấp, duyệt, từ chối, thu hồi quyền hỗ trợ; đội vận hành giữ danh sách
   nhân viên hỗ trợ và giao người (ticket 01).
2. Một request mang mã quyền dựng ngữ cảnh hỗ trợ: vai rỗng, scope đóng dấu, không cache,
   kiểm MFA, kiểm hạn mỗi request; route không khai mở thì từ chối; mỗi truy cập một audit
   (ticket 02).
3. Web: nhân viên hỗ trợ thấy quyền được giao, mở một quyền, API client gửi mã quyền,
   shell có chỗ cắm băng hỗ trợ, context đăng ký trang đích (ticket 03).

## Trong phạm vi

- Bảng `platform.support_staff`, `platform.support_grants`; vòng đời yêu cầu, duyệt, từ
  chối, giao người, thu hồi, hết hạn.
- `SupportScopeCatalog` (context đăng ký bộ scope ở composition root), kiểm tập con scope
  theo người cấp, `SupportResourcePort` (context trả nhãn tài nguyên).
- Ngữ cảnh hỗ trợ, `RequireAccessContextOrSupport`, hằng `SUPPORT_ALLOWED_ROUTES` (rỗng trên
  `main`), audit `support.access`.
- Membership từ chối nhân viên hỗ trợ ở mọi đường đặt membership.
- Route audit thêm lọc `support_grant_id`, `actor_display_name`, `actor_kind`.
- Console `/platform` (giao người, danh sách nhân viên); trang `/support` "Quyền hỗ trợ của
  tôi"; phiên quyền hỗ trợ ở web, header, chỗ cắm băng, registry trang đích.
- ADR trung tính "Customer-granted support access".

## Ngoài phạm vi

| Việc                                                                 | Vì sao                                                             |
| -------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Kiểm xung đột lợi ích khi giao người                                 | Câu hỏi còn mở 2; P1. Cờ `support_access` mặc định tắt giữ điều đó |
| Gia hạn bằng một nút; drawer nhật ký riêng mỗi quyền                 | Gia hạn là một quyền mới; P1                                       |
| Bộ scope, nhãn tài nguyên, route mở cho hỗ trợ của context           | Context tự khai ở composition root trong nhánh của nó              |
| Băng hỗ trợ, menu và trang chặn trong ngữ cảnh hỗ trợ                | Chữ và phạm vi thuộc context; ticket 03 chỉ dựng chỗ cắm           |
| Lọc approval, run, audit theo workspace; `GET /runs/{id}` kiểm scope | `approval-audit-and-workspace` ticket 02                           |

## Dữ liệu

Migration Alembic (id hex ngẫu nhiên). Thời điểm `timestamptz`; tên ràng buộc theo
`NAMING_CONVENTION`; mọi FK khai `ON DELETE` và có index phía mình; grant đi cùng migration,
`test_privileges.py` khẳng định; `test_rls_coverage.py` thấy bảng mới.

**`platform.support_staff`** (không tenant, như `platform.platform_operators`): `user_id` (PK,
FK `platform.users` ON DELETE CASCADE), `note` (≤ 200), `added_by` (FK users ON DELETE SET
NULL, index), `added_at`. Ghi: chỉ vai provisioner. `dw_app`: SELECT.

**`platform.support_grants`**:

| Cột                                                          | Ràng buộc                                                                                                                                         |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id` uuid PK; `code` text                                    | `code` dạng `SG-0012`, sinh theo tenant; UNIQUE (`tenant_id`, `code`)                                                                             |
| `tenant_id`, `workspace_id`                                  | FK CASCADE; index                                                                                                                                 |
| `resource_type` text, `resource_id` uuid NULL                | CHECK `resource_type ~ '^[a-z_]{1,40}$'`; `resource_id` NULL khi và chỉ khi `resource_type = 'workspace'`. Không FK sang bảng context             |
| `resource_label` text                                        | Do context sinh lúc tạo qua `SupportResourcePort`; ≤ 200                                                                                          |
| `scope_set_key` text, `scope_set_label` text                 | Khóa trong danh mục do context đăng ký                                                                                                            |
| `scopes` text[]                                              | NOT NULL, không rỗng; đóng dấu lúc cấp. Mọi người đọc sau dùng bản đóng dấu này                                                                   |
| `reason` text                                                | 1–300 ký tự                                                                                                                                       |
| `duration_hours` int                                         | CHECK 1–336                                                                                                                                       |
| `status` text                                                | CHECK IN (`pending_approval`, `pending_assignment`, `active`, `rejected`, `revoked`). "Hết hạn" và "mất hiệu lực" là trạng thái suy ra, không lưu |
| `requested_by`, `requested_at`                               | FK users SET NULL                                                                                                                                 |
| `granted_by`, `granted_at`                                   | Có khi và chỉ khi status ở `pending_assignment`, `active`, `revoked` (sau duyệt)                                                                  |
| `rejected_by`, `rejected_at`, `reject_reason`                | Có khi và chỉ khi `rejected`; lý do 1–300                                                                                                         |
| `staff_user_id`, `assigned_by`, `activated_at`, `expires_at` | Có khi và chỉ khi đã giao; `expires_at = activated_at + duration_hours`                                                                           |
| `revoked_by`, `revoked_at`                                   | Có khi và chỉ khi `revoked`                                                                                                                       |

- RLS tenant, FORCE, policy `tenant_isolation_support_grants`; repository lọc thêm
  workspace của người gọi. Index `(tenant_id, workspace_id, requested_at DESC, id)` cho danh
  sách; `(staff_user_id, status)`.
- `dw_app`: SELECT, INSERT, UPDATE chỉ cột phía khách (duyệt, từ chối, thu hồi); không
  DELETE. Vai provisioner: SELECT, UPDATE cột giao người.
- Hàm `platform.support_grant_for_staff(p_grant uuid)` và `platform.support_grants_for_staff()`
  `SECURITY DEFINER`, chỉ trả dòng có `staff_user_id = current_setting('app.principal_id')`,
  không nhận user id từ tham số; `REVOKE ALL FROM PUBLIC`, `GRANT EXECUTE` cho `dw_app`.
- `platform.roles`: thêm `support.request` vào `org_admin`. Scope `support.grant` không gán ở
  nền tảng: context gán cho vai của nó.
- Cờ tính năng `support_access`: không thêm vào gói nào; tenant bật qua
  `entitlements.feature_overrides`; seed nền tảng không bật.
- Audit: `support.grant.requested`, `.approved`, `.rejected`, `.assigned`, `.revoked`,
  `support.access`. `actor_id` là user id của người làm; `details.support_grant_id` có ở mọi
  sự kiện của một quyền; không lý do, không nội dung trong `details`.

## API (`/api/v1`)

| Route                                                      | Scope / ngữ cảnh                                                            | Ghi chú                                                                                                                            |
| ---------------------------------------------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `GET /support/catalog`                                     | `support.grant` hoặc `support.request`                                      | Bộ scope đã đăng ký: khóa, nhãn, loại tài nguyên. Không trả danh sách scope                                                        |
| `GET /support/grants`                                      | `support.grant` (mọi quyền của workspace); `support.request` (chỉ của mình) | Trạng thái suy ra tính bằng cùng hàm với bộ dựng ngữ cảnh                                                                          |
| `POST /support/grants`                                     | như trên                                                                    | Có `support.grant` → kiểm tập con, `pending_assignment`; chỉ `support.request` → `pending_approval`. Body có `staff_user_id` → 422 |
| `POST /support/grants/{id}/approve`, `/reject`, `/revoke`  | `support.grant`                                                             | Duyệt kiểm tập con theo người duyệt; từ chối bắt lý do; thu hồi có hiệu lực từ request kế tiếp                                     |
| `GET /support/my-grants`                                   | danh tính đã xác minh, có trong `support_staff`                             | Qua hàm `SECURITY DEFINER`; không cần tenant                                                                                       |
| `GET /platform/support-staff`, `POST`, `DELETE /{user_id}` | Platform Operator                                                           | Thêm theo email của user đã có                                                                                                     |
| `GET /platform/support-requests`, `POST /{id}/assign`      | Platform Operator                                                           | Chỉ `pending_assignment`; người được giao phải ở `support_staff`                                                                   |
| `GET /audit/events` (có sẵn)                               | không đổi kiểm scope ở lát này                                              | Thêm lọc `support_grant_id`, `actor_display_name`, `actor_kind`                                                                    |
| `GET /auth/bootstrap` (có sẵn)                             | danh tính                                                                   | Thêm `is_support_staff`                                                                                                            |

## Kiểm soát

Mỗi kiểm soát có test âm, và ticket ghi rằng test đỏ khi gỡ kiểm soát (failure-modes #3).

| #    | Kiểm soát                                                                  | Test âm                                                                                                         |
| ---- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| SA1  | Chỉ `support.grant` cấp; `support.request` chỉ tạo yêu cầu chờ duyệt       | Vai không có `support.*` tạo → 403; `org_admin` tạo → `pending_approval`, không bao giờ `active` khi chưa duyệt |
| SA2  | Người cấp chỉ trao tập con scope mình giữ; mất vai thì quyền mất hiệu lực  | Thiếu một scope của bộ → 403; gỡ vai người cấp → request kế tiếp của nhân viên 403 `support_grant_ended`        |
| SA3  | Khách không chọn người; chỉ giao cho danh tính trong `support_staff`       | Body có `staff_user_id` → 422; giao cho user không ở danh sách → 409                                            |
| SA4  | Ngữ cảnh hỗ trợ: vai rỗng, scope đóng dấu, không cache, không hợp vai      | Nhân viên giữ `platform_admin` ở tenant khác gọi route đòi scope ngoài bản đóng dấu → 403                       |
| SA5  | MFA bắt buộc                                                               | Token không có yếu tố thứ hai → 403 `support_mfa_required`                                                      |
| SA6  | Hết hạn, thu hồi kiểm mỗi request                                          | Đồng hồ qua `expires_at` → 403; thu hồi rồi gọi lại → 403                                                       |
| SA7  | Route mặc định từ chối ngữ cảnh hỗ trợ                                     | Test duyệt bảng route; `GET /runs/{id}`, `GET /approvals`, `/knowledge/*` với header → 403                      |
| SA8  | Danh mục từ chối bộ có scope nền tảng nhạy cảm                             | Đăng ký bộ có `approvals.decide` → lỗi                                                                          |
| SA9  | Nhân viên hỗ trợ không thành member của tenant khách                       | `POST /admin/members` với email nhân viên hỗ trợ → 409                                                          |
| SA10 | Mọi truy cập dưới quyền có audit khách đọc được; nhân viên không đọc audit | Ba request → ba `support.access`; nhân viên gọi `/audit/events` → 403                                           |
| SA11 | Cờ `support_access` mặc định tắt                                           | Tenant không có cờ: tạo quyền → 403; quyền đã có không dựng được ngữ cảnh                                       |
| SA12 | Hàm "quyền của tôi" chỉ trả quyền của chính người gọi                      | `app.principal_id` là người khác → 0 dòng                                                                       |
| SA13 | Cách ly tenant, workspace                                                  | Người tenant B và người ws2 đọc, duyệt, thu hồi quyền của ws1 tenant A theo id → 404, không dòng nào đổi        |

## Tiêu chí xong

- Ba ticket `resolved`; `make ci` xanh; integration của `dw_platform` và `apps/api` xanh.
- Mỗi kiểm soát SA1–SA13 có test âm đã thấy đỏ khi gỡ kiểm soát (ghi trong Comments).
- `reviewing-feature-security` và `reviewing-deployment-security` trên header mới, route
  mới, cấu hình realm.

## Phụ thuộc

Ticket 01 và 02 không chờ gì ngoài lát này. Ticket 03 (web) chờ các ticket `web-ui/antd-shell`
03, 05, 06, 09 trên `main` (đường dẫn đầy đủ ở dòng `Blocked by` của ticket). Context dùng quyền hỗ trợ khai bộ scope, route mở cho hỗ
trợ (kèm test âm) và trang đích ở nhánh của nó, sau khi merge lát này.

## Câu hỏi còn mở

1. **Claim MFA.** Realm chưa có OTP. Ticket 02 bật OTP bắt buộc cho nhóm nhân viên hỗ trợ và
   đo claim `amr`/`acr` Keycloak trả thật trước khi viết bộ kiểm (failure-modes #4).
2. **Xung đột lợi ích.** Demo giao người không kiểm xung đột; chỉ tenant có cờ
   `support_access` dùng được. Kiểm thật là P1, cần Đạt chốt cách làm.
3. **Nối danh tính theo email.** Nhân viên hỗ trợ đăng nhập qua realm của đội; đường nối
   theo email của `identity_provisioning.py` phải đóng trước khi bật SSO cho khách.

## Danh sách ticket

| #   | Ticket                                                                                                  | Status                            | Blocked by                               |
| --- | ------------------------------------------------------------------------------------------------------- | --------------------------------- | ---------------------------------------- |
| 01  | [Quyền hỗ trợ: bảng, vòng đời, route phía khách và đội hỗ trợ](issues/01-support-grants-lifecycle.md)   | resolved (web bước 7 ở phiên web) | —                                        |
| 02  | [Ngữ cảnh hỗ trợ: MFA, route mặc định từ chối, audit](issues/02-support-access-context.md)              | lõi xong; còn bước 10 (audit)     | 01                                       |
| 03  | [Web: phiên quyền hỗ trợ, trang "Quyền hỗ trợ của tôi", chỗ cắm băng](issues/03-support-web-session.md) | ready-for-agent                   | 01, 02, web-ui/antd-shell 03, 05, 06, 09 |
