# 12 — Bước 8: email chốt NCC và kiểm thư trả lời

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/07-supplier-messages.md, .claude/plans/supply-chain/ai-automation/issues/11-step-7-bm04-prefill.md
Area: supply-chain

## Mục tiêu

AI soạn email chốt từ BM04 đã duyệt; khi thư trả lời được kéo vào, AI đọc điều khoản và code so với BM04; TP Cung ứng duyệt chuyển bước với danh sách khác biệt.

## Việc cần làm

1. Bản nháp tin `supplier_confirmation` (ticket 07) từ BM04.
2. Schema trích `supplier_confirmation_email`: giá, tiền tệ, MOQ, thời gian, quy cách, bao bì.
3. Code so từng trường với BM04 → khớp / khác / thiếu; đề xuất `confirm_with_supplier` kèm
   bảng so.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Thư có câu "đồng ý mọi điều khoản" mà giá khác: vẫn báo khác giá.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 8; QE-09; ADR 0029.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
