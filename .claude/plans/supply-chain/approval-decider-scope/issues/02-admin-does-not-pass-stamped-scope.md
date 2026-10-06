# 02 — `platform_admin` không thỏa `required_scope` đã đóng dấu

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/approval-decider-scope/issues/01-required-scope.md
Area: supply-chain

## Mục tiêu

Một approval có `required_scope` (ví dụ BGĐ duyệt mẫu) chỉ người giữ đúng scope đó quyết
được; vai `platform_admin` (người vận hành nền tảng) không còn đi qua bằng quy tắc admin
chung (Đạt giao quyết, 2026-10-06, QO-8). Ứng viên đưa ngược `codebase` cùng lát A.

## Việc cần làm

1. Trong `ApproveAndResumeService.decide`, kiểm `required_scope` bằng một hỏi tường minh
   "context có scope này" (không qua nhánh admin của `ScopeAuthorizationService`), vẫn sau
   `approvals.decide` và trước mọi lệnh ghi. Một nơi quyết, đặt tên rõ (ví dụ
   `holds_stamped_scope`), dùng lại ở đường đọc.
2. Đường đọc cùng nguồn: API trả `can_decide` (hoặc web dùng đúng hàm đó) để nút ở
   `/approvals` khóa với admin thiếu scope, kèm lý do bằng chữ.
3. Sửa ADR 0020 phần hệ quả "platform_admin đi qua".

## Tiêu chí chấp nhận

- [ ] Unit + integration: `platform_admin` có `approvals.decide` mà thiếu scope đóng dấu bị
      từ chối; approval vẫn `pending`; run không tiếp tục. Mutation: trả về nhánh admin thì
      test đỏ.
- [ ] Người giữ scope vẫn quyết được; approval không đóng dấu: admin vẫn quyết như hôm nay.
- [ ] Vitest `/approvals`: admin thiếu scope thấy nút khóa kèm lý do.
- [ ] `make ci` xanh; integration `dw_agent_runtime` xanh.

## Comments
