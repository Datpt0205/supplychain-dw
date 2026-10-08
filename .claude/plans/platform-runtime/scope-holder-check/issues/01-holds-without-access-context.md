# 01 — `SqlScopeHolders.holds` theo id, không cần `AccessContext`

Status: resolved (2026-10-08, nhánh `feat/platform-tickets`)
Blocked by: —
Area: platform-runtime

## Mục tiêu

Một bên gọi chỉ có tenant, workspace (từ nguồn đã xác minh, vd state đã đóng dấu) và id một
người hỏi được người đó có giữ một scope ở workspace đó không, qua cùng truy vấn thành viên và
`effective_scopes` mà `holding` dùng (spec, Hiện trạng).

## Việc cần làm

1. `packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py`: tách
   truy vấn thành viên của `holding` thành một hàm riêng trong cùng tệp (lọc `tenant_id`,
   `workspace_id`, tenant `active`, và một `user_id` khi được truyền). `holding` gọi hàm đó,
   giữ nguyên chữ ký và kết quả.
2. Thêm `SqlScopeHolders.holds(tenant_id, workspace_id, user_id, scope) -> bool`:
    - mở `tenant_session(self.session_factory, TenantScope(tenant_id, workspace_id))`;
    - gọi hàm ở bước 1 với `user_id`; trả `True` khi có đúng dòng thành viên đó và `scope` thuộc
      `effective_scopes(...)` của nó;
    - không có thành viên, tenant không `active`, `scope` rỗng: `False` (đóng khi sai,
      failure-modes #7);
    - không ngoại lệ theo vai (spec, Ngoài phạm vi).
3. Doc comment của `holds`: tenant và workspace phải đến từ nguồn bên gọi đã xác minh, không từ
   thân request; hàm không kiểm việc đó.

## Tiêu chí chấp nhận

Integration `packages/python/dw_platform/tests/integration/test_scope_holders.py` (PostgreSQL
thật, kết nối `dw_app`, hai tenant, tenant A có ws1 và ws2):

- [x] Người giữ một vai mang scope `x.do` ở ws1: `holds(A, ws1, user, "x.do")` là `True`; cùng
      người ở ws2, nơi họ không có vai đó: `False`; người không có thành viên ở ws1: `False`.
- [x] Scope đến từ permission set (không từ vai): `True`.
- [x] Tenant A bị khóa (`status` khác `active`): `holds` là `False`, `holding` trả rỗng.
- [x] Hỏi bằng tenant B cho người và ws1 của tenant A: `False` (RLS và điều kiện tenant).
- [x] Hai lời gọi liên tiếp, tenant A rồi tenant B, trên cùng pool: lời thứ hai không thấy
      thành viên của tenant A.
- [x] Đồng thuận: với mọi thành viên của fixture, `user in holding(ctx, ws, {s})` khi và chỉ
      khi `holds(tenant, ws, user, s)`.
- [x] Mutation (ghi vào Comments): bỏ điều kiện `workspace_id` trong hàm chung thì ca ws2 đỏ;
      bỏ điều kiện tenant `active` thì ca tenant bị khóa đỏ.
- [x] `rg "memberships" packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py`
      chỉ ra một truy vấn (không có truy vấn thứ hai cho `holds`).
- [x] `make ci` xanh.

## Nguồn

- `packages/python/dw_platform/src/dw_platform/adapters/persistence/scope_holders.py:24-63`;
  `membership_lookup.py:29-51` (`effective_scopes`); `tenant_session.py:23-27, 68-79`.
- `packages/python/dw_platform/src/dw_platform/application/access_context.py:1-6`;
  `application/authorization.py:47-50`.
- `CLAUDE.md` "Tenancy and authorization"; `.claude/rules/code-quality.md` (DRY: một hàm trả
  lời một câu hỏi); `.claude/rules/failure-modes.md` #2, #3, #7.

## Comments

- 2026-10-08 (agent): làm cùng ticket 02 trong một commit. `_members(session, tenant_id,
workspace_id, user_id=None)` là truy vấn thành viên duy nhất của tệp; `holding` và `holds`
  cùng gọi nó và `effective_scopes` (qua `_scopes_of`). `holds` trả `False` khi không đúng một
  dòng thành viên, tenant không `active`, hoặc `scope` rỗng; không ngoại lệ theo vai.
  `rg "memberships" scope_holders.py` chỉ ra một truy vấn (các dòng `tables.memberships.c.*`
  của cùng một `select`, cộng docstring).
- Test: `packages/python/dw_platform/tests/integration/test_scope_holders.py`, 10 ca, kết nối
  `dw_app`, tenant và vai tạo mới mỗi test (khóa tenant A ở một test không lây sang test khác).
  Ca pool dùng `pool_size=1` để chắc hai lời gọi đi trên cùng một kết nối, và kiểm
  `current_setting('app.tenant_id', true)` rỗng sau khi trả kết nối. 10 passed.
- Mutation: bỏ `memberships.workspace_id == workspace_id` trong `_members` → 6 đỏ, gồm
  `test_holds_answers_per_workspace` (người chỉ có ở ws2 thành `True` ở ws1) và hai ca khác
  workspace của `holding`. Bỏ `tenants.status == "active"` → `test_a_locked_tenant_has_no_holders`
  đỏ. Khôi phục, 10 passed.
