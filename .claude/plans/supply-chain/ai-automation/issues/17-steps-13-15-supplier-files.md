# 17 — Bước 13–15: file NCC, QC, vận chuyển, đóng cont

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md, .claude/plans/supply-chain/ai-automation/issues/14-step-10-purchase-order.md
Area: supply-chain

## Mục tiêu

AI đọc lịch sản xuất, báo cáo QC, packing list, hóa đơn, B/L, giấy báo hàng đến; code đối chiếu với dòng PO và cập nhật ETD/ETA; QC và Logistics duyệt.

## Việc cần làm

1. `DocumentType` mới: `production_schedule`, `qc_report`, `packing_list`, `bill_of_lading`,
   `arrival_notice`, `certificate_of_origin`; cột ETD, ETA, số cont trên Hồ sơ PO.
2. Bản nháp test trước SX như bước 3; trích QC (AQL, lỗi) → gợi ý đạt/không cạnh ô trống;
   bản nháp `rework_request`.
3. Packing list/B/L đối chiếu số lượng theo SKU; ETA từ giấy báo → nhắc trước; skill
   `customs_file` liệt kê hồ sơ thiếu.
4. Đóng cont: khi Elmich trả lời QE-15 là bước riêng thì thêm trạng thái; tới đó số cont và
   ảnh đóng cont nằm trên `pass_qc`.
5. Cập nhật NCC dán chữ hiện có giữ nguyên; file kéo vào đi qua lane trích.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Số lượng packing list khác PO: phát hiện theo SKU.
- [ ] Báo cáo QC có câu "PASS" mà số lỗi vượt AQL: gợi ý theo số, không theo chữ.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 13–15; QO-6, QE-14, QE-15.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
