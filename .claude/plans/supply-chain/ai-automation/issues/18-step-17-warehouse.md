# 18 — Bước 17: phiếu nhập kho và đối chiếu số đếm

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/17-steps-13-15-supplier-files.md
Area: supply-chain

## Mục tiêu

AI soạn phiếu nhập kho từ dòng PO và packing list; Kho nhập số đếm; code đối chiếu, soạn biên bản chênh lệch và thư khiếu nại NCC.

## Việc cần làm

1. `DocumentType` mới `warehouse_receipt`, `discrepancy_report`; số đếm theo dòng
   (`po_case_line_receipts`, chỉ thêm).
2. Bước vật lý: `complete` có ô số đếm để trống, gợi ý là số giao.
3. Chênh lệch → bản nháp biên bản và tin gửi NCC (ticket 07).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Số đếm rỗng không duyệt được.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 17; HR3.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
