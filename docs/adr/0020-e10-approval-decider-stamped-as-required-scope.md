---
status: Proposed
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
