# 15 — Bước 11 và 16: đề nghị đặt cọc, thanh toán, đối chiếu

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/14-step-10-purchase-order.md
Area: supply-chain

## Mục tiêu

AI soạn đề nghị đặt cọc và thanh toán; đọc PI, hóa đơn, UNC; code đối chiếu số tiền, tiền tệ, người thụ hưởng với PO và danh mục NCC; Kế toán trả tiền ở ngân hàng rồi duyệt.

## Việc cần làm

1. `DocumentType` mới: `proforma_invoice`, `commercial_invoice`, `bank_transfer_receipt`.
2. Mẫu `deposit_request`, `payment_request`; số tiền do code (cọc = tổng × %, cuối = tổng −
   đã cọc).
3. Trích PI, hóa đơn, UNC; số tài khoản che trước mô hình, code so với
   `supplier_bank_accounts` (khác → phát hiện "tài khoản thụ hưởng khác danh mục").
4. Đối chiếu ba chiều PO / hóa đơn / packing list (khi ticket 17 có).
5. Bước vật lý: `confirm_deposit`, `confirm_payment` có ô số tiền đã trả để trống.
6. `po_payments` ghi khi duyệt; `deposit_docs`, `payment_docs` trở thành bắt buộc theo
   policy tenant (QE-02).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Số tài khoản không bao giờ trong request tới gateway.
- [ ] Tài khoản khác danh mục: phát hiện đỏ trên approval; không tự chặn, người quyết.
- [ ] Người thiếu `commercial.read` không thấy số tiền.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 11, 16; ADR 0021 sửa đổi 2026-10-06 (lưu 10 năm).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
