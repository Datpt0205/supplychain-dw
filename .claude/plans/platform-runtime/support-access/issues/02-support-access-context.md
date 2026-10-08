# 02 — Ngữ cảnh hỗ trợ: MFA, route mặc định từ chối, audit

Status: ready-for-agent (lõi xong 8/10/2026; còn bước 10, xem Comments)
Blocked by: 01
Area: platform-runtime

## Mục tiêu

Nhân viên hỗ trợ dùng danh tính của chính họ, có xác thực hai lớp, và chỉ mang scope đóng
dấu trong quyền. Route nào không khai mở cho hỗ trợ thì từ chối ngữ cảnh hỗ trợ: thiếu scope
chỉ chặn được route có kiểm scope, còn `GET /runs/{id}` hôm nay không kiểm scope nào, nên
mặc định từ chối là chỗ chặn thật (failure-modes #5).

## Việc cần làm

1. **Claim xác thực.** `VerifiedIdentity` (`application/ports.py:26`) thêm `auth_methods:
frozenset[str]` (từ `amr`) và `acr: str | None`. Adapter OIDC
   (`adapters/identity/keycloak.py`) đọc hai claim; adapter dev (`dev_token.py`) đọc từ token
   dev (chỉ profile không triển khai, như hôm nay).
2. **Keycloak.** Trong `infra/keycloak/dw-realm.json`: nhóm `dw-support`, OTP là required
   action cho thành viên nhóm, mapper đưa `amr` (hoặc `acr` theo mức) vào access token.
   Trước khi viết bộ kiểm, đăng nhập thật một user của nhóm qua compose và ghi vào Comments
   token trả claim nào, giá trị gì, có và không có OTP (failure-modes #4). Bộ kiểm dựa vào
   giá trị đo được.
3. **Bộ dựng** `SupportAccessContextFactory` (`dw_platform/application/support_access.py`):
   danh tính đã xác minh → có trong `support_staff` → có yếu tố thứ hai (thiếu → 403
   `support_mfa_required`) → `platform.support_grant_for_staff(id)` → `grant_effective_state`
   của ticket 01 là `active` → tenant có cờ `support_access`. Mã lỗi:
   `support_staff_required`, `support_mfa_required`, `support_grant_ended` (kèm `ended_at`,
   `ended_reason` `expired|revoked|ineffective`), `support_access_not_enabled`.
4. **AccessContext** thêm `support: SupportScope | None` (`grant_id`, `code`,
   `resource_type`, `resource_id`, `scope_set_key`). Ngữ cảnh hỗ trợ: tenant, workspace lấy
   từ quyền (không từ header của client); `roles` rỗng; `scopes` bằng bản đóng dấu;
   `principal_id` là nhân viên. Không qua `CachingMembershipLookup`. Không hợp với vai nào
   của nhân viên, kể cả `platform_admin`.
5. **Dependency** (`apps/api/src/dw_api/dependencies/auth.py`). `RequireAccessContext` gặp
   header `X-DW-Support-Grant` thì 403 `support_context_not_allowed`. Thêm
   `RequireAccessContextOrSupport`: có header thì dựng ngữ cảnh hỗ trợ, không thì như cũ. Sau
   khi dựng, ghi một audit `support.access` (`actor_id` nhân viên; `details`:
   `support_grant_id`, `method`, mẫu route, tham số đường dẫn là id; không query string,
   không body).
6. **Bảng route cho phép.** Hằng `SUPPORT_ALLOWED_ROUTES` ở composition root, rỗng trên
   `main`. Test duyệt `app.routes`: tập route có `RequireAccessContextOrSupport` phải bằng
   đúng hằng đó. Context thêm route vào hằng trong cùng thay đổi với test âm của nó.
7. **Kiểm lại trước khi ghi.** Một hàm công khai `recheck_support_grant(context)` dùng cùng
   bước 3 để thao tác dài (tải tệp lớn) gọi ngay trước bước ghi cuối.
8. **"Quyền của tôi".** Hàm `SECURITY DEFINER` `platform.support_grants_for_staff()` (không
   tham số; đọc `app.principal_id`) trả mã, tên công ty, tên workspace, nhãn phạm vi, nhãn chế
   độ, `resource_type`, `resource_id`, `scope_set_key`, `expires_at`, trạng thái suy ra, của
   quyền giao cho người gọi, kết thúc trong 30 ngày gần nhất. Route `GET /support/my-grants`
   (danh tính đã xác minh, phải ở `support_staff`, không cần tenant).
9. **Bootstrap.** `GET /auth/bootstrap` thêm `is_support_staff`.
10. **Route audit.** `GET /audit/events` thêm lọc `support_grant_id`, trường
    `actor_display_name` (join `platform.users`; không khớp → `null`), `actor_kind` (`user`,
    `support` khi `details.support_grant_id` có). Kiểm scope và lọc workspace của route là
    ticket 02 của `approval-audit-and-workspace`, không làm ở đây.
11. Wiring; `make generate-contracts`.

## Tiêu chí chấp nhận

Integration (`dw_platform/tests/integration` và `apps/api/tests`; một route test có
`RequireAccessContextOrSupport` đăng ký trong fixture):

- [ ] SA4: nhân viên có membership `platform_admin` ở một tenant khác dựng ngữ cảnh từ quyền
      ở tenant A → `context.roles == frozenset()`, `scopes` đúng bản đóng dấu; route test đòi
      scope ngoài bản đóng dấu → 403 (không qua nhánh `admin_role` của
      `authorization.py:47-49`). Không có khóa cache nào cho ngữ cảnh hỗ trợ sau request.
- [ ] SA5: token không có yếu tố thứ hai → 403 `support_mfa_required`. Gỡ kiểm → test đỏ.
- [ ] SA6: `expires_at` đã qua (đồng hồ giả) → 403 `support_grant_ended`, `expired`; thu hồi
      rồi gọi lại → `revoked`; gỡ vai người cấp → `ineffective`. `recheck_support_grant` sau
      thu hồi → lỗi cùng mã.
- [ ] SA7: với header hợp lệ, `GET /runs/{id}`, `GET /approvals`, `GET /knowledge/...`,
      `GET /audit/events`, `GET /admin/members` → 403 `support_context_not_allowed`. Test bảng
      route xanh với hằng rỗng; thêm `RequireAccessContextOrSupport` vào một route mà không
      thêm vào hằng → test đỏ.
- [ ] SA10: ba request dưới ngữ cảnh hỗ trợ → ba dòng `support.access` có
      `support_grant_id`; lọc theo `support_grant_id` thấy đúng ba dòng; nhân viên gọi
      `/audit/events` → 403.
- [ ] SA11: tenant tắt cờ sau khi cấp → ngữ cảnh không dựng được.
- [ ] SA12: `support_grants_for_staff()` với `app.principal_id` là người khác → 0 dòng;
      `support_grant_for_staff` cho quyền của người khác → 0 dòng. Test hỏi catalog: hai hàm
      là `SECURITY DEFINER`, `PUBLIC` không có quyền chạy.
- [ ] SA13: header hỗ trợ kèm `X-Tenant-Id`, `X-Workspace-Id` của tenant B hay ws2 → ngữ cảnh
      lấy tenant, workspace từ quyền; route test không trả dòng nào của tenant B hay ws2.
- [ ] Comments: claim Keycloak đo được, bản token đã che chữ ký.
- [ ] `reviewing-deployment-security` trên header mới, route mới, cấu hình realm.
- [ ] `make ci` xanh.

## Nguồn

- `apps/api/src/dw_api/dependencies/auth.py:67`; `dw_platform/application/access_context.py`;
  `adapters/persistence/caching_lookup.py:100`; `application/cache.py` (cache fail-open);
  `apps/api/src/dw_api/routes/v1/runs.py:41-58`, `approvals.py:44-76`, `audit.py:39-72`,
  `auth.py:32-38`.
- `spec.md` của lát này: Ngữ cảnh hỗ trợ (Mục tiêu 2), SA4–SA7, SA10–SA13, câu hỏi còn mở 1.

## Comments

- 8/10/2026 (nhánh `feat/platform-tickets`, quyết tạm theo ủy quyền của Đạt).
  **Đo Keycloak 26.7.2** (đăng nhập thật qua compose, PKCE, mã TOTP thật, realm
  tạm đã xóa): mặc định token có mật khẩu và token có mật khẩu + OTP giống hệt
  nhau, `acr: "1"`, `amr: []`. Bật mapper AMR trên `dw-web` và đặt
  authentication reference (`pwd` cho form mật khẩu, `otp` cho form OTP, max
  age 43200): chỉ mật khẩu ra `amr: ["pwd"]`, nhân viên hỗ trợ ra
  `["pwd","otp","otp"]`; `acr` vẫn `"1"`. Lần đăng nhập đầu của nhân viên mới bị
  ép cài OTP nhưng token của phiên đó chỉ có `["pwd"]`, nên bị từ chối và phải
  đăng nhập lại một lần. `dw-realm.json`: nhóm `dw-support` mang vai realm
  `support-staff`, luồng `dw browser` có nhánh con "Support staff OTP" (vai →
  form OTP bắt buộc); tệp đã được nhập thử thành realm mới và đăng nhập đúng như
  đo. `--import-realm` không cập nhật realm đã có: môi trường đã chạy phải áp
  tay. Bản token đã che chữ ký không giữ lại (realm tạm đã xóa).
- **Đã làm:** `VerifiedIdentity.auth_methods` (từ `amr`) và `acr` ở cả hai
  verifier; `AccessContext.support` (`SupportScope`); `SupportAccessContextFactory`
  (nhân viên hỗ trợ → yếu tố thứ hai `otp`/`hwk`/`mfa` trong `amr` → quyền giao
  cho chính người đó qua `support_grant_for_staff` → `grant_effective_state`
  là `active` → tenant có `support_access`), mã lỗi trong
  `details.reason_code`: `support_staff_required`, `support_mfa_required`,
  `support_grant_ended` (kèm `ended_reason` `expired|revoked|ineffective`,
  `ended_at`), `support_access_not_enabled`, `support_context_not_allowed`;
  quyền của người khác là 404. Ngữ cảnh: tenant, workspace từ quyền, vai rỗng,
  scope đóng dấu, không cache. `recheck` cho thao tác dài.
  `RequireAccessContext` gặp `X-DW-Support-Grant` thì 403
  `support_context_not_allowed`; `RequireAccessContextOrSupport` kiểm
  `(method, path)` trong `SUPPORT_ALLOWED_ROUTES` (rỗng, `wiring.py`) ngay trong
  dependency, rồi dựng ngữ cảnh và ghi một `support.access` (phương thức, mẫu
  route, id trong đường dẫn; không query, không body). Migration
  `f1576bf82a5a`: hai hàm `SECURITY DEFINER` đọc `app.principal_id`, `REVOKE ALL
FROM PUBLIC`, `dw_app` EXECUTE. `GET /support/my-grants` (chỉ nhân viên hỗ
  trợ). `GET /auth/bootstrap` có `is_support_staff`. Header thêm vào CORS.
- **Test:** unit `test_support_context.py` 10 ca (SA4 vai rỗng và không qua
  nhánh admin, chỉ nhân viên hỗ trợ, SA5 hai ca, quyền của người khác 404, SA6
  hết hạn đúng tại `expires_at`, thu hồi kể cả `recheck`, người cấp mất
  `support.grant`, SA11, audit không có body). API `test_support_routes.py`:
  bộ duyệt route thấy route dùng dependency (chứng minh test đỏ được), tập route
  dùng dependency bằng đúng `SUPPORT_ALLOWED_ROUTES`, SA7 với `GET /runs/{id}`,
  `/approvals`, `/knowledge/documents`, `/audit/events`, `/admin/members`,
  `/support/grants` → 403 `support_context_not_allowed`, và dependency tự từ
  chối route thiếu trong danh sách. Integration
  `test_support_staff_access.py`: SA12 (người khác và kết nối không gắn ai đọc 0
  dòng; catalog: hai hàm là `SECURITY DEFINER`, `PUBLIC` không chạy được,
  `dw_app` chạy được), SA4/SA10 từ quyền thật (vai rỗng, scope đóng dấu; không
  phải nhân viên, thiếu OTP, nhân viên khác bị từ chối; ba lần truy cập là ba
  dòng `support.access` có `support_grant_id`).
- **Mutation:** gỡ từ chối header trong `RequireAccessContext` → SA7 đỏ; gỡ kiểm
  yếu tố thứ hai → SA5 đỏ cả hai ca; gỡ kiểm danh sách trong dependency → test
  route thiếu danh sách đỏ. Chưa mutation ở mức SQL cho hàm `SECURITY DEFINER`
  (ca "nhân viên khác đọc 0 dòng" là test sẽ đỏ nếu bỏ điều kiện
  `staff_user_id`, nhưng chưa chạy thử với hàm đã sửa).
- **Còn lại:** bước 10 (route audit: lọc `support_grant_id`,
  `actor_display_name`, `actor_kind`; SA10 phần "lọc thấy đúng ba dòng" và
  "nhân viên gọi `/audit/events` → 403" — ca 403 đã có trong SA7); áp realm cho
  môi trường đã chạy; kiểm xung đột lợi ích khi giao người (spec, câu hỏi 2).
  Web (ticket 03) chưa làm.
