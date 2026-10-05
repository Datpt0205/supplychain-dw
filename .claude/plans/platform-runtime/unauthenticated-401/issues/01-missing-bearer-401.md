# 01 — Thiếu hoặc hỏng token Bearer trả 401, không phải 403

Status: needs-triage
Blocked by: —
Area: platform-runtime

## Mục tiêu

Gọi API không kèm `Authorization: Bearer ...` (hoặc token không kiểm được) hiện trả
`403 permission_denied` trên mọi route, vì `dw_kernel.http_auth.bearer_token` ném
`PermissionDeniedError("missing bearer token")` và `dw_api/errors.py` ánh xạ mã đó sang 403.
HTTP phân biệt hai việc: 401 là "chưa xác thực" (đăng nhập lại sẽ chữa), 403 là "đã biết
là ai, và không được". Gộp hai việc làm một client không biết nên đăng nhập lại hay báo
thiếu quyền. Tìm thấy khi viết ticket Z1 của sản phẩm Elmich
(`supply-chain/zalo-channel/issues/01-zalo-link.md`, tiêu chí route).

## Việc cần làm

1. Một mã lỗi riêng cho "chưa xác thực" (ví dụ `ErrorCode.UNAUTHENTICATED`) ánh xạ 401,
   có header `WWW-Authenticate: Bearer`; `bearer_token` và lối token không kiểm được ném mã
   đó. `PermissionDeniedError` giữ cho "đã xác thực, không được".
2. Đọc lại luồng 401 của web (`apps/web/lib/session.ts:124-160`, chuyển trang đăng nhập
   một lần) trước khi đổi: một route hôm nay trả 403 cho request thiếu token sẽ bắt đầu
   kích hoạt chuyển trang đó.
3. Cập nhật mọi test đang khẳng định 403 cho request thiếu token (tìm `missing bearer`).

## Tiêu chí chấp nhận

- [ ] Không có header, header sai scheme, token rỗng: 401 với `WWW-Authenticate`.
- [ ] Token hợp lệ mà thiếu quyền: vẫn 403 `permission_denied`.
- [ ] Mutation: trả lại `PermissionDeniedError` trong `bearer_token` thì test đỏ.
- [ ] Contract openapi sinh lại; web không chuyển trang hai lần trong một loạt 401.

## Comments

- Chưa ai quyết đổi; ghi lại để Z1 không tích một tiêu chí chưa đạt.
