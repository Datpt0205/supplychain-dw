# 02 — `SqlScopeHolders.holding` nhận `tenant_id`, không cần `AccessContext`

Status: resolved (2026-10-08, nhánh `feat/platform-tickets`)
Blocked by: .claude/plans/platform-runtime/scope-holder-check/issues/01-holds-without-access-context.md
Area: platform-runtime

## Mục tiêu

Một lane worker báo tin cho người giữ một scope ở một workspace (vd nhắc việc theo lịch) không
có `AccessContext`: nó chỉ có tenant, workspace đọc từ dòng nó đang xử lý. `holding` hiện đòi
`AccessContext` dù chỉ đọc `context.tenant_id` (spec, Hiện trạng), nên lane không gọi được.
Ticket này đổi `holding` sang nhận `tenant_id`, giữ chung truy vấn thành viên với `holds`.

## Việc cần làm

1. `packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py`: đổi chữ
   ký thành `holding(tenant_id, workspace_id, scopes) -> list[UUID]`. Thân hàm giữ nguyên:
   `tenant_session` với `TenantScope(tenant_id, workspace_id)`, hàm truy vấn thành viên mà
   ticket 01 tách ra (không truy vấn thứ hai), `effective_scopes` cho từng thành viên, sắp
   theo `user_id`. `scopes` rỗng trả rỗng.
2. Bỏ import `AccessContext` khỏi tệp khi không còn ai dùng.
3. Trên `main` chưa ai gọi `holding` (`rg SqlScopeHolders` chỉ ra định nghĩa, 3/10/2026), nên
   không có chỗ gọi phải sửa. Có chỗ gọi mới từ lúc đó thì sửa cùng thay đổi này.
4. Doc comment của `holding`: tenant và workspace phải đến từ nguồn bên gọi đã xác minh (dòng
   đã gắn tenant trong database, state đã đóng dấu, `AccessContext.tenant_id`), không từ thân
   request; hàm không kiểm việc đó. Giống `holds` của ticket 01.
5. Test đồng thuận của ticket 01 gọi `holding` theo chữ ký mới.

## Tiêu chí chấp nhận

Integration `packages/python/dw_platform/tests/integration/test_scope_holders.py` (PostgreSQL
thật, kết nối `dw_app`, hai tenant, tenant A có ws1 và ws2):

- [x] Người giữ scope `x.do` ở ws1 có trong `holding(A, ws1, {"x.do"})`; người chỉ giữ scope đó
      ở ws2 thì không; người có thành viên ở ws1 mà không giữ scope thì không.
- [x] Khác tenant: `holding(B, ws1 của A, {"x.do"})` trả rỗng (RLS và điều kiện tenant).
- [x] Khác workspace: `holding(A, ws2, {"x.do"})` không chứa người chỉ giữ scope ở ws1.
- [x] Tenant A không `active`: `holding(A, ws1, ...)` trả rỗng.
- [x] Hai lời gọi liên tiếp, tenant A rồi tenant B, trên cùng pool: lời thứ hai không thấy thành
      viên của tenant A.
- [x] Đồng thuận với `holds` vẫn đúng với chữ ký mới.
- [x] Mutation (ghi vào Comments): bỏ điều kiện `workspace_id` trong hàm chung thì ca khác
      workspace đỏ; bỏ điều kiện tenant `active` thì ca tenant không `active` đỏ.
- [x] `rg "AccessContext" packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py`
      không ra gì.
- [x] `make ci` xanh.

## Nguồn

- `packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py:27-63`;
  `membership_lookup.py:29-51` (`effective_scopes`); `tenant_session.py:23-27, 68-79`.
- Spec, câu hỏi mở 1 (chuyển vào ticket này).
- `CLAUDE.md` "Tenancy and authorization"; `.claude/rules/code-quality.md` (DRY: một hàm trả
  lời một câu hỏi); `.claude/rules/failure-modes.md` #2, #3, #7.

## Comments

- 2026-10-08 (agent): `holding(tenant_id, workspace_id, scopes)`; import `AccessContext` đã bỏ
  (`rg AccessContext scope_holders.py` không ra gì). Không có bên gọi nào phải sửa (`rg
SqlScopeHolders` chỉ ra định nghĩa và test). Doc comment ghi tenant, workspace phải đến từ
  nguồn đã xác minh. Test đồng thuận gọi chữ ký mới, cho ws1, ws2 và hai scope.
- Mutation: như ticket 01 (cùng hàm chung): bỏ điều kiện `workspace_id` →
  `test_holding_in_another_workspace_excludes_ws1_only_holders` và
  `test_holding_lists_the_holders_of_one_workspace` đỏ; bỏ điều kiện `active` →
  `test_a_locked_tenant_has_no_holders` đỏ (ca đó kiểm cả `holding` rỗng).
