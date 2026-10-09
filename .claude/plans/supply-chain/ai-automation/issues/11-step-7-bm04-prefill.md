# 11 — Bước 7: BM04 điền sẵn, nêu khoảng trống

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md, .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Khi hồ sơ vào `profile_in_progress`, AI điền bản nháp BM04 có trường từ hồ sơ, biên bản đạt và báo giá/spec của NCC; ô thiếu hoặc mâu thuẫn được nêu, không đoán (slide 8).

## Việc cần làm

1. Prompt `draft_bm04@1.0.0` + skill `bm04_guide`; nguồn: hồ sơ, trích xuất biên bản,
   `supplier_quotation`.
2. Code: hai nguồn khác giá trị → mâu thuẫn; trường bắt buộc của schema tenant không có
   nguồn → khoảng trống.
3. Duyệt `complete_profile`: phiên bản `product_profiles` + file BM04 dựng từ mẫu thành
   chứng từ `product_profile_bm04`.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Hồ sơ không có báo giá: giá là khoảng trống, không số.
- [ ] Hai nguồn giá khác nhau: mâu thuẫn nêu cả hai trích dẫn.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Slide 8 PoC; ADR 0026 (E15); QE-05.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
