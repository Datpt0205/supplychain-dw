# 01 — Thiếu hoặc hỏng token Bearer trả 401, không phải 403

Status: resolved (2026-10-08, nhánh `feat/security-debts`)
Blocked by: —
Area: platform-runtime

Ticket gốc viết ở repo sản phẩm đầu tiên (2026-10-05, `needs-triage`); chép về đây khi làm.

## Mục tiêu

Gọi API không kèm `Authorization: Bearer ...` (hoặc token không kiểm được) trả
`403 permission_denied` trên mọi route, vì `dw_kernel.http_auth.bearer_token` ném
`PermissionDeniedError`. HTTP phân biệt hai việc: 401 là "chưa xác thực" (đăng nhập lại sẽ
chữa), 403 là "đã biết là ai, và không được". Gộp hai việc làm client không biết nên đăng
nhập lại hay báo thiếu quyền.

## Việc đã làm

- `ErrorCode.UNAUTHENTICATED` (`unauthenticated`) → 401, `UnauthenticatedError`.
  `bearer_token` (thiếu header, sai scheme, token rỗng) và cả hai verifier (dev HS256,
  Keycloak) khi token không kiểm được ném lỗi này. `PermissionDeniedError` giữ cho "đã xác
  thực, không được" (ví dụ không có membership ở tenant được hỏi).
- Exception handler gắn `WWW-Authenticate: Bearer` cho MỌI phản hồi 401 (kể cả
  `tenant_context_missing`, vốn đã 401).
- Web: `clientOptions().fetchImpl` chỉ chuyển về trang đăng nhập khi request CÓ mang token
  (phiên chết giữa chừng), vẫn một lần cho cả loạt 401. Request không mang token (các gọi
  của chính trang đăng nhập) nay cũng nhận 401; chuyển trang ở đó sẽ tải lại trang đăng
  nhập mãi.
- `ErrorCode` của TS thêm `Unauthenticated`; test mirror sẵn có (`test_error_codes.py`) bắt
  được khi thiếu.

## Tiêu chí chấp nhận

- [x] Không có header, header rỗng, sai scheme, token rỗng, token không kiểm được: 401
      `unauthenticated` với `WWW-Authenticate: Bearer` (`test_me_endpoint.py`, 6 ca).
- [x] Token hợp lệ mà thiếu quyền: vẫn 403 `permission_denied`, không có challenge.
- [x] Mutation: `bearer_token` ném lại `PermissionDeniedError` → 17 đỏ; bỏ header challenge
      → 6 đỏ; verifier ném lại `PermissionDeniedError` → 1 đỏ; web bỏ điều kiện "có token"
      → vitest 1 đỏ.
- [x] Contract openapi sinh lại (không đổi: mã lỗi không nằm trong schema); web không chuyển
      trang hai lần trong một loạt 401 (vitest).

## Comments

- Quyết định tạm (Đạt giao): header challenge cho mọi 401, không riêng `unauthenticated`
  (RFC 9110 §15.5.2 bắt buộc với 401).
