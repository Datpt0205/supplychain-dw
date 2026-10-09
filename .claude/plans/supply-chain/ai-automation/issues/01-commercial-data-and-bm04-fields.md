# 01 — Dữ liệu thương mại và BM04 là trường có kiểu (E15)

Status: ready-for-agent
Blocked by: —
Area: supply-chain

## Mục tiêu

BM04 thành biểu mẫu trong ứng dụng; Hồ sơ PO, cọc, thanh toán và NCC có các trường mà bản nháp và phép kiểm của các ticket sau cần; giá sau scope riêng, không bao giờ qua Zalo.

## Việc cần làm

1. **Migration** (id hex ngẫu nhiên): `product_profiles` (phiên bản chỉ thêm, FK ghép tới
   hồ sơ phát triển `ON DELETE CASCADE`, có index; cột đơn giá `numeric`, tiền tệ CHECK ISO
   4217, MOQ, `lead_time_days`, Incoterm CHECK, `attributes jsonb`); trên `po_cases`: tiền
   tệ, Incoterm, điều khoản thanh toán, `deposit_percent` CHECK 0–100, ngày giao dự kiến;
   `po_case_lines.unit_price`; `po_payments` (loại CHECK `deposit | final`, số tiền > 0, hạn,
   ngày trả, `document_id`), chỉ thêm; `supplier_contacts`, `supplier_bank_accounts` (phiên
   bản chỉ thêm). Grant trong migration.
2. **Policy** `supply_chain_bm04_schema@1.0.0` (schema của `attributes`, tenant ghi đè qua
   `PolicyOverridePort`); BM04 mặc định trung tính.
3. **Scope** `supply_chain.commercial.read|write`; vai nào giữ là migration dữ liệu (mặc
   định `sc_operator`, `sc_finance`, `sc_bod`). Thiếu scope: trường giá trả `null` kèm
   `redacted: true`, không bao giờ 0.
4. **Zalo:** bộ dựng tóm tắt duyệt, câu trả lời hỏi đáp và thông báo không đọc trường giá
   (một chủ: danh sách trường được phép của Zalo).
5. **Web:** form BM04 trên hồ sơ phát triển (antd Form), khối thương mại trên Hồ sơ PO,
   khóa kèm lý do khi thiếu scope. Tải lên file BM04 vẫn được.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Người thiếu `commercial.read` nhận `redacted`, không số; người có scope thấy giá.
- [ ] Câu trả lời Zalo về một Hồ sơ PO có giá không chứa giá (test, đột biến: thêm trường giá vào danh sách được phép thì đỏ).
- [ ] `attributes` sai schema của tenant bị 422; override schema của tenant có hiệu lực.
- [ ] Tài khoản ngân hàng không xuất hiện trong bất kỳ biến prompt nào (test duyệt mọi prompt đăng ký).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0026 (E15); `docs/products/elmich/process.md` mục 2 (BM04: thông số, hình ảnh, giá).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
