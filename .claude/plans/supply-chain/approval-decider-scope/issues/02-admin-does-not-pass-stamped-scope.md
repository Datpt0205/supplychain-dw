# 02 — `platform_admin` không thỏa `required_scope` đã đóng dấu

Status: resolved
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

- [x] Unit + integration: `platform_admin` có `approvals.decide` mà thiếu scope đóng dấu bị
      từ chối; approval vẫn `pending`; run không tiếp tục. Mutation: trả về nhánh admin thì
      test đỏ.
- [x] Người giữ scope vẫn quyết được; approval không đóng dấu: admin vẫn quyết như hôm nay.
- [x] Vitest `/approvals`: admin thiếu scope thấy nút khóa kèm lý do.
- [x] `make ci` xanh; integration `dw_agent_runtime` xanh.

## Comments

- 2026-10-06, agent: làm xong.
    - **Một chủ:** `holds_stamped_scope(context, required_scope)` trong
      `dw_platform/application/authorization.py` (không dấu thì đúng; có dấu thì
      `context.scopes` phải chứa scope đó, không vai nào thay). `permission_denied(...)` cùng
      file là dạng lỗi duy nhất, `ScopeAuthorizationService.require` cũng dùng nó.
    - **Đường ghi:** `ApproveAndResumeService.decide` thay `authorization.require(action=
required_scope)` bằng `holds_stamped_scope`, vẫn sau `approvals.decide`, trước mọi ghi
      và `resume`; ném cùng `PermissionDeniedError` ("action not permitted", `action` là
      scope đóng dấu). Không dấu: admin vẫn quyết. Người yêu cầu vẫn tự rút được.
    - **Đường đọc:** `ApproveAndResumeService.may_decide` (`is_allowed(approvals.decide)` và
      `holds_stamped_scope`) → `ApprovalView.can_decide` ở cả danh sách, chi tiết và phản hồi
      quyết. `/approvals` khóa nút theo `can_decide` (bỏ `missingScope` dựa trên `hasScope`
      của phiên, vốn cho `platform_admin` qua mọi scope); chữ lý do giữ nguyên. Contracts
      sinh lại (`make generate-contracts`), `openapi.json` đổi lại LF.
    - ADR 0020: gạch Hệ quả "platform_admin qua được" và quyết định tạm 2, thêm Sửa đổi
      2026-10-06. Ticket Z5 không nhắc admin, không đổi.
    - **Test mới/sửa:** unit `test_approval_flow.py`
      (`test_platform_admin_does_not_pass_the_stamped_scope[approve|reject]`,
      `test_platform_admin_holding_the_stamped_scope_decides`,
      `test_platform_admin_still_decides_an_unstamped_request`,
      `test_may_decide_answers_as_decide_does` 7 ca; bỏ
      `test_platform_admin_passes_the_stamped_scope_through_the_same_rule`); API
      `test_approvals_endpoint.py` (`test_can_decide_is_the_servers_answer_in_the_list_and_the_detail`
      6 ca, `test_platform_admin_without_the_stamp_is_refused_over_http`); integration
      `test_approval_decider_scope.py::test_platform_admin_does_not_pass_the_stamp` (approve
      và reject bị từ chối, `pending`, `version` 1, 0 dòng decision, run `waiting_approval`;
      rồi người giữ scope quyết, run `completed`) và
      `::test_platform_admin_still_decides_an_unstamped_approval`; vitest "locks both for
      platform_admin without the stamped scope (QO-8)" (mock `hasScope` cho admin qua mọi
      scope như thật).
    - **Mutation (đều đỏ rồi khôi phục):** M1 trả nhánh admin vào `decide` (kiểm dấu bằng
      `authorization.is_allowed`): 3 unit/API đỏ (`..._does_not_pass_the_stamped_scope`
      ×2, `..._refused_over_http`) và integration `test_platform_admin_does_not_pass_the_stamp`
      đỏ. M2 thêm `or "platform_admin" in context.roles` vào `holds_stamped_scope`: 5 đỏ (thêm
      `may_decide[admin-without-stamp]`, `can_decide[admin-without-stamp]`). M3 trang quay
      về `hasScope(required_scope)`: vitest admin đỏ (1 failed, 8 passed).
    - **Chạy (6/10/2026):** `make lint` xanh (ruff, prettier, eslint; cảnh báo cũ);
      `make typecheck` xanh (mypy 484 file, tsc); `make test-unit` 2030 passed, 3 skipped;
      `make test-architecture` 9 kept, 0 broken, invariants ok; `make test-contract` 5
      passed; `make release-manifest-check` OK. Integration (`make infra-up`, `.env`):
      `dw_agent_runtime` 72 passed (file `test_approval_decider_scope.py` 11), `dw_platform`
      207 passed; `make infra-down` sau đó. Vitest web: 30 file, 284 test passed (`/approvals`
      9).
