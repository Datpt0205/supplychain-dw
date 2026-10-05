# 01 — `required_scope` trên approval: đóng dấu lúc tạo, kiểm lúc quyết, đọc ở `/approvals`

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

Chỉ người giữ scope đã đóng dấu trên approval mới quyết được nó (spec, Mục tiêu). Ứng
viên đưa ngược. Bị chặn bởi port chỉ để migration nối sau head của chuỗi sản phẩm.

## Việc cần làm

1. **Migration nền tảng** (id hex ngẫu nhiên): `platform.approval_requests` thêm
   `required_scope text NULL` với CHECK dạng tên scope
   (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`). `ApprovalRequest` và repository thêm trường.
2. **Đóng dấu:** `_create_approval` đọc `payload.get("required_scope")`; giá trị không
   khớp CHECK làm run lỗi, không bỏ qua (đóng khi sai, failure-modes #7). Ghi docstring:
   payload do node viết từ policy, không bao giờ từ output mô hình.
3. **Kiểm:** trong `decide`, khi `approve` hoặc khi người quyết không phải người yêu
   cầu, sau `approvals.decide`, nếu `required_scope` khác NULL thì
   `authorization.require(action=required_scope, ...)`. Kiểm trước khi ghi quyết định
   và trước `runner.resume`.
4. **API:** `GET /approvals` và `GET /approvals/{id}` trả `required_scope`; contracts
   TypeScript thêm trường.
5. **Web:** `/approvals` khóa nút quyết khi người xem thiếu `required_scope`, với lý
   do bằng chữ trong `Tooltip` và cạnh nút ("Chỉ người có quyền … được quyết yêu cầu
   này"); không tự suy quyền từ tiền tố.

## Tiêu chí chấp nhận

- [ ] **Test âm (unit service):** người có `approvals.decide` mà thiếu
      `required_scope` gọi duyệt thì `PermissionDeniedError`; approval vẫn `pending`;
      `runner.resume` không được gọi; không có dòng decision.
- [ ] Cùng người khi có scope: duyệt được. `required_scope` NULL: hành vi như hôm nay
      (test có sẵn vẫn xanh). Người yêu cầu rút yêu cầu của mình không cần scope.
- [ ] **Test âm (API, integration):** approval của tenant khác hoặc workspace khác trả
      not found, kể cả với người có scope.
- [ ] Payload interrupt có `required_scope` sai dạng: run lỗi, không tạo approval.
- [ ] Mutation: bỏ lệnh kiểm ở bước 3 thì test âm đỏ (ghi vào Comments). Kiểm ở web bị
      bỏ thì API vẫn từ chối (test API trên).
- [ ] Vitest `/approvals`: người thiếu scope thấy nút khóa kèm lý do; người có scope
      thấy nút bật.
- [ ] `make ci` xanh; openapi sinh lại.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3, "Not yet solved" (không có luật "chỉ BGĐ quyết loại X").
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "Platform gap".
- Trên `main`: `approval_flow.py:96-140`, `langgraph_runner.py:698-713`,
  `domain/approval.py:44-58`.

## Comments
