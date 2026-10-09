# 14 — Bước 10: PO nháp

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md, .claude/plans/supply-chain/ai-automation/issues/12-step-8-supplier-confirmation.md
Area: supply-chain

## Mục tiêu

Vào `order_requested`, AI soạn PO từ BM04, điều khoản NCC đã chốt, SKU và số lượng; code tính tổng; Cung ứng duyệt `create_po`.

## Việc cần làm

1. Mẫu `purchase_order`; số PO theo quy tắc tenant hoặc người nhập.
2. Code tính tổng dòng, tổng PO, cọc theo `deposit_percent`.
3. Duyệt `create_po`: trường thương mại ghi vào Hồ sơ PO, chứng từ `purchase_order`; thông
   báo Kế toán.
4. QE-06: nếu Elmich muốn BGĐ duyệt PO trên ngưỡng, bật trong approval matrix (không code).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Tổng do mô hình trả khác code: bản nháp bị từ chối.
- [ ] Thiếu điều khoản: khoảng trống, PO vẫn soạn được để người điền.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Slide 4 PoC (PO nháp); ADR 0017; ADR 0026.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
