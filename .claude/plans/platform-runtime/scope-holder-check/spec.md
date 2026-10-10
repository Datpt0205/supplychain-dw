# Hỏi một người có giữ một scope ở một workspace không, khi không có `AccessContext`

Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Một node workflow chạy tiếp run sau quyết định của
người khác cần hỏi "người quyết có giữ scope X ở workspace của việc này không" ngay lúc áp.
Lúc đó không có `AccessContext` của người quyết: node chỉ có tenant, workspace đã đóng dấu
trong state và id người quyết đọc từ approval. Nền tảng đã có chủ của câu "ai trong workspace
giữ scope X" (`SqlScopeHolders`), nhưng hàm đó đòi một `AccessContext`.

## Mục tiêu

`SqlScopeHolders.holds(tenant_id, workspace_id, user_id, scope) -> bool` trả lời câu hỏi cho
một người mà không cần `AccessContext`, dùng chung truy vấn thành viên và `effective_scopes`
với `holding`, nên "ai được báo" và "ai được làm" không lệch nhau.

## Hiện trạng (đã kiểm trong code ngày 3/10/2026)

- `SqlScopeHolders.holding(context: AccessContext, workspace_id, scopes) -> list[UUID]`
  (`packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py:24-63`)
  chỉ đọc `context.tenant_id`. Nó mở `tenant_session` với
  `TenantScope(tenant_id, workspace_id)`, lọc thành viên của workspace với tenant `active`,
  rồi tính scope của từng thành viên bằng `effective_scopes`.
- `tenant_session` (`tenant_session.py:68-79`) gắn `app.tenant_id`, `app.workspace_id` bằng
  `set_config(..., true)` (`:23-27`), nên kết nối trả về pool không mang phạm vi sang lượt
  sau.
- `effective_scopes(session, role_keys, permission_set_keys)` (`membership_lookup.py:29-51`)
  không cần `AccessContext`, nhưng không trả lời câu hỏi: bên gọi phải tự đọc dòng thành viên
  và trạng thái tenant, tức chép lại truy vấn của `holding` (failure-modes #2).
- `AccessContext` (`application/access_context.py:1-6`) chỉ được dựng từ token đã xác minh
  cộng thành viên trong database. Dựng một cái từ giá trị đóng dấu trong state là tạo ngữ
  cảnh không qua token.
- Trên `main` chưa ai gọi `SqlScopeHolders` (`rg SqlScopeHolders` chỉ ra định nghĩa) và chưa
  có test nào cho nó.

## Trong phạm vi

- Ticket 01: `holds(...)`; truy vấn thành viên tách thành một hàm mà `holding` và `holds` cùng
  gọi; test tích hợp cho cả hai.
- Ticket 02: `holding` nhận `tenant_id` thay cho `AccessContext`, để một lane worker (không có
  `AccessContext`) lấy được danh sách người giữ scope; dùng chung truy vấn thành viên của ticket 01.

## Ngoài phạm vi

- Ngoại lệ theo vai: `holds` không cho `platform_admin` đi tắt như
  `ScopeAuthorizationService.is_allowed` (`application/authorization.py:47-50`). Câu hỏi là
  "thành viên này có scope này không", cùng nghĩa với `holding`.

## Tiêu chí xong

- Ticket 01 và 02 `resolved`; `make ci` xanh; `dw_platform` integration xanh.
- Mỗi kiểm soát mới có một test đã thấy đỏ khi gỡ kiểm soát (ghi trong Comments của ticket).

## Phụ thuộc

Không bị chặn bởi gì.

## Câu hỏi còn mở

Không còn. Câu hỏi 1 cũ (`holding` trong lane worker) đã thành ticket 02 (3/10/2026): đã có
bên gọi là lane worker báo tin cho người giữ scope.

## Danh sách ticket

| #   | Ticket                                                                                                               | Status   | Blocked by |
| --- | -------------------------------------------------------------------------------------------------------------------- | -------- | ---------- |
| 01  | [`SqlScopeHolders.holds` theo id, không cần `AccessContext`](issues/01-holds-without-access-context.md)              | resolved | —          |
| 02  | [`SqlScopeHolders.holding` nhận `tenant_id`, không cần `AccessContext`](issues/02-holding-without-access-context.md) | resolved | 01         |
