# 13 — Bước 9: đề xuất mã hàng, SKU, kiểm danh mục

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/11-step-7-bm04-prefill.md, .claude/plans/supply-chain/onboarding/issues/01-import-suppliers-catalogue-users.md
Area: supply-chain

## Mục tiêu

Vào `item_coding`, hệ thống đề xuất mã hàng theo quy tắc của tenant và SKU từ biến thể BM04, kiểm trùng với danh mục đã nạp, soạn tờ trình ký.

## Việc cần làm

1. Policy `supply_chain_item_code_rule@1.0.0` (mẫu mã; trống tới QE-11 thì không đề xuất
   mã, chỉ SKU). Mã do code sinh, không mô hình.
2. SKU từ `attributes` biến thể của BM04; số lượng dự kiến nếu có.
3. Kiểm trùng: `item_codes`, `skus`, danh mục nạp (ON-01).
4. Bản nháp `item_code_submission` (`DocumentType` mới) đi kèm `submit_for_signoff`.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Mã trùng với danh mục đã nạp bị đánh dấu; DB vẫn từ chối trùng trong app.
- [ ] Không có quy tắc mã: không đề xuất mã (không đoán).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0018; QE-11; slide 8 PoC (đối chiếu danh mục).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
