# 16 — Bước 12: MKT tối thiểu và kiểm bản in (E17)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md, .claude/plans/supply-chain/ai-automation/issues/11-step-7-bm04-prefill.md
Area: supply-chain

## Mục tiêu

MKT nhận gói (BM04, HDSD, maquette) và nộp nội dung bao bì trong ứng dụng; AI kiểm từng bản in thiết kế với BM04 và luật nhãn, soạn yêu cầu sửa màu/thiết kế.

## Việc cần làm

1. Vai `sc_mkt`, duty `mkt`; bước con `send_mkt_pack` (`ordering`), `submit_packaging_content`
   (`mkt`); policy duty Hồ sơ PO phiên bản mới + override cũ (mẫu `STEPS_ADDED_AFTER`).
2. Gói MKT dựng tự động khi màu được duyệt; thông báo cho người giữ duty `mkt` thay dòng
   "đã báo TP MKT".
3. Trích `packaging_design` (PDF/ảnh): tên, mã, SKU, mã vạch, kích thước, chất liệu, xuất xứ,
   cảnh báo; code so BM04; skill `label_rules` liệt kê nội dung bắt buộc thiếu.
4. Bản nháp `colour_revision_request`, `design_revision_request`.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] `sc_mkt` không thấy giá, không làm bước Cung ứng.
- [ ] Override duty lưu trước migration vẫn nạp được.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0028 (E17); packaging-design ticket 01; QE-03.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
