# 08 — Bước 1: đọc danh sách SP đề xuất

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

PIC kéo một danh sách SP đề xuất (xlsx, pdf, ảnh trang catalogue) vào; AI tách thành N bản nháp đề xuất, kiểm trùng, gợi ý Category và mức ưu tiên; PIC duyệt từng dòng.

## Việc cần làm

1. Prompt `extract_proposal_list@1.0.0`: mỗi dòng tên SP, mã đề xuất nếu có, NCC nếu có,
   ảnh tham chiếu, trích dẫn.
2. Code: Category chỉ nhận khóa trong danh sách tenant; kiểm trùng mã đề xuất, mã hàng, SKU
   và danh mục đã nạp (ON-01 khi có); mức ưu tiên là gợi ý có lý do tới khi QE-13 có thang.
3. Bản nháp dùng `proposal_drafts` hiện có hoặc bảng lô; duyệt từng dòng gọi đúng handler
   `propose` (PIC = người duyệt).
4. Web: bảng duyệt lô (antd Table, sửa ô, bỏ dòng).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Dòng trùng mã bị đánh dấu, không tạo hồ sơ thứ hai (DB vẫn là chủ).
- [ ] Category ngoài danh sách bị từ chối, không đoán gần đúng.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` mục 3.2 hàng 1; Z4b (`product_proposal_understanding`).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
