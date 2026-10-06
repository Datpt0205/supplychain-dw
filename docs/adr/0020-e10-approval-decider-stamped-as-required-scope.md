---
status: Accepted
date: 2026-10-05
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide 96-140, is_strict 37-44
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/adapters/langgraph_runner.py # _create_approval 698-713
    - ../../packages/python/dw_platform/src/dw_platform/domain/approval.py # ApprovalRequest 44-58
    - ../products/elmich/process.md#32-bảng-17-bước # bước 6, 9
---

# E10. Ai được quyết một approval là `required_scope`, đóng dấu lúc tạo và kiểm lúc quyết

Bước 6 chỉ BGĐ được duyệt; bước 9 BGĐ và Kế toán ký. Hôm nay
`ApproveAndResumeService.decide` chỉ đòi `approvals.decide`, nên ai có quyền duyệt
chung đều quyết được mọi approval của workspace. Tiền tố nghiêm chỉ chặn người yêu
cầu tự duyệt và đòi nhận xét.

**Quyết định (phần chung, ứng viên đưa ngược):**

- `platform.approval_requests` thêm cột `required_scope text NULL` (CHECK theo dạng
  tên scope). `ApprovalRequest` thêm trường cùng tên.
- **Đóng dấu lúc tạo:** `_create_approval` đọc `required_scope` từ payload interrupt
  của node, như đã đọc `approval_type`. Node lấy giá trị từ policy của tenant lúc yêu
  cầu. Payload do code của node viết, không bao giờ do mô hình.
- **Kiểm lúc quyết:** khi duyệt, hoặc từ chối yêu cầu của người khác, `decide` đòi
  cả `approvals.decide` lẫn `required_scope` (nếu khác NULL), qua cùng
  `authorization.require`. Người yêu cầu rút yêu cầu của mình như hôm nay.
- **Đường đọc và đường ghi cùng nguồn:** API trả `required_scope` của từng approval;
  `/approvals` hiện nút quyết bị khóa kèm lý do bằng chữ khi người xem thiếu scope
  đó. Trang không tự suy ai được quyết.
- NULL giữ hành vi hôm nay, nên approval có sẵn không đổi.

Giá trị của Elmich: `supply_chain.approve.bod` (vai `sc_bod`) cho bước 6 và bước ký
của BGĐ; `supply_chain.approve.accounting` cho bước ký của Kế toán, cấp cho vai
`sc_finance` có sẵn (Kế toán của bước 11 và 16) thay vì một vai `sc_accounting` mới,
trừ khi Elmich tách hai người (QE-16). Scope của từng approval và thứ tự ký nằm ở một policy
`supply_chain_product_approvals@1.0.0`, tenant ghi đè được (QE-10).

## Phương án đã cân nhắc

- **Tra policy lúc quyết.** Bác: đổi policy giữa chừng sẽ đổi người được quyết một
  approval đang chờ. Dấu đóng lúc tạo là quyết định của quá khứ.
- **Kiểm trong node của context sau khi resume.** Bác: run đã tiếp tục và quyết định đã
  ghi trước khi bị từ chối; chặn phải ở nơi quyết định được ghi (failure-modes #5).
- **Một tiền tố cho mỗi vai trong `strict_approval_prefixes`.** Bác: tiền tố nói
  "nghiêm hay không", không nói "ai".

## Hệ quả

- Test âm bắt buộc: người có `approvals.decide` mà thiếu `required_scope` bị từ chối
  và run không tiếp tục; approval của tenant khác trả not found.
- Danh sách `/approvals` vẫn hiện approval cho người không quyết được; ẩn chúng là
  việc khác, chưa làm.
- **Khác workspace không thuộc quyết định này.** RLS của `platform.approval_requests` và
  `approval_decisions` chỉ lọc tenant, và lát A không đổi chúng. Approval của workspace
  khác trong cùng tenant là việc của
  `.claude/plans/platform-runtime/approval-audit-and-workspace/issues/02` (lọc ở
  repository, không đổi RLS; đổi RLS sang dạng workspace là một quyết định riêng).
- **`platform_admin` qua được `required_scope`**, vì lệnh kiểm đi qua cùng
  `authorization.require`, nơi `ScopeAuthorizationService.is_allowed` có một luật admin
  duy nhất. Ghim bằng test unit
  `test_platform_admin_passes_the_stamped_scope_through_the_same_rule`.
- Dạng tên scope có một chủ: CHECK `ck_approval_requests_required_scope`. Giá trị sai dạng
  hoặc không phải chuỗi làm INSERT lỗi, run kết thúc `failed`, không có dòng approval.
- Dấu ghi một lần: `add` ghi, `save` không; `dw_app` chỉ còn UPDATE trên `status`,
  `decided_at`, `version` của `approval_requests` (migration `5d3965984679`).

## Sửa đổi 2026-10-05 (tạm, lát A; chờ Đạt duyệt ở QO-2)

Năm quyết định tạm của lead khi làm lát A, ghi lại ở đây vì chúng thu hẹp hoặc thêm vào
phần Quyết định ở trên:

1. Test âm "approval của tenant khác trả not found" giữ nguyên; phần "hoặc workspace
   khác" của ticket A chuyển sang platform-runtime/approval-audit-and-workspace/02. Không
   đổi policy RLS của hai bảng approval.
2. `platform_admin` qua được dấu (Hệ quả ở trên); không thêm luật nào chặn admin riêng.
3. API trả thêm `requested_by_me: bool` cạnh `required_scope`. Lý do: người yêu cầu rút
   yêu cầu của mình không cần scope, và trang không biết yêu cầu nào là của người xem nếu
   thiếu nó. Là một boolean tính ở server, nên không id thành viên nào khác tới trình
   duyệt. Trang khóa nút bằng `required_scope` và `hasScope` của phiên; không suy từ tiền
   tố `approval_type`.
4. CHECK của cột là chủ duy nhất của dạng tên scope (không có kiểm ở tầng domain để dùng
   lại); runner chuyển giá trị nguyên vẹn, không ép kiểu, không bỏ.
5. Thu UPDATE toàn bảng của `dw_app` trên `approval_requests`, cấp lại UPDATE theo cột
   (`status`, `decided_at`, `version`): Postgres không thu được một cột khỏi quyền UPDATE
   toàn bảng. Việc này cũng khóa `approval_type`, `requested_by`, `payload`, vốn không code
   nào sửa.

## Sửa đổi 2026-10-05 (tạm, platform-runtime/approval-audit-and-workspace/02; chờ Đạt duyệt ở QO-2)

Phần "khác workspace" mà Sửa đổi 1 ở trên chuyển đi nay đã làm, vẫn không đổi policy RLS:

1. `decide` đọc approval trong workspace của người quyết (`get(..., workspace_id=)` của
   repository, lấy từ `AccessContext`). Approval của workspace khác trong cùng tenant là
   `NotFoundError` "approval request not found", trước lệnh kiểm `approvals.decide` và
   `required_scope`, trước mọi ghi và trước `resume`: người giữ cả hai scope ở workspace
   khác cũng không quyết được. Test: `test_approval_workspace.py` của `dw_agent_runtime`.
2. Run chạy tiếp với `workspace_id` của run (`RunRecord.workspace_id`, đọc từ dòng
   `worker_runs`), không của người quyết, như roles, scopes và autonomy đã đọc từ run.
3. `GET /approvals` và `GET /approvals/{id}` chỉ trả approval của workspace người gọi; cờ
   khóa nút theo `required_scope` không đổi.

## Sửa đổi 2026-10-05 (tạm, lát S2 của giai đoạn 1; chờ Đạt duyệt ở QO-2)

1. **Payload resume có `decided_by`.** `ApproveAndResumeService.decide` đặt
   `decided_by = str(context.principal_id)` vào payload resume, từ context đã xác minh của
   người quyết, dựng ngay trong `decide`; không bao giờ chép từ payload của approval (đó
   là giá trị interrupt của graph). Run vẫn tiếp tục với quyền của người yêu cầu như
   trước; `decided_by` chỉ cho graph biết AI quyết, để ghi người đó làm actor (graph duyệt
   mẫu của Hồ sơ phát triển, ADR 0016 sửa đổi S2). Test:
   `test_the_resume_names_the_decider_from_their_verified_context`,
   `test_an_interrupt_payload_naming_a_decider_cannot_override_the_real_one`. Ứng viên đưa
   ngược lên nền tảng (ADR 0011): thay đổi nằm trong `dw_agent_runtime`, không phụ thuộc
   context nào.
2. **Giá trị đầu tiên của Elmich** đã có: approval
   `supply_chain.product_action.bod_review` đóng dấu `required_scope` đọc từ
   `supply_chain_product_approvals@1.0.0` lúc tạo; test ghi đè của tenant: approval tạo
   sau dùng giá trị mới, approval đang chờ giữ dấu cũ
   (`test_a_tenant_override_reaches_reviews_raised_after_it_only`).
