# 01 — Dữ liệu thương mại và BM04 là trường có kiểu (E15)

Status: resolved
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

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; xem Comments.)_
- [x] Người thiếu `commercial.read` nhận `redacted`, không số; người có scope thấy giá.
- [x] Câu trả lời Zalo về một Hồ sơ PO có giá không chứa giá (test, đột biến: thêm trường giá vào danh sách được phép thì đỏ).
- [x] `attributes` sai schema của tenant bị 422; override schema của tenant có hiệu lực.
- [x] Tài khoản ngân hàng không xuất hiện trong bất kỳ biến prompt nào (test duyệt mọi prompt đăng ký).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0026 (E15); `docs/products/elmich/process.md` mục 2 (BM04: thông số, hình ảnh, giá).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-01 (agent).** Đã làm (không Docker theo yêu cầu của Đạt):

- Migration `82221a867e62` (hex của alembic, một head, đọc lại bằng mắt và parse bằng
  `pglast` qua `alembic upgrade --sql`; **chưa chạy trên Postgres**): `product_profiles`,
  `po_payments`, `supplier_contacts`, `supplier_bank_accounts` (workspace RLS FORCE, chỉ
  SELECT + INSERT cho `dw_app`, FK ghép có index, CHECK tiền tệ ISO 4217 / Incoterm / loại
  thanh toán / giá cần tiền tệ); cột thương mại trên `po_cases`, `po_case_lines.unit_price`
  (GRANT UPDATE cột); scope `supply_chain.commercial.read|write` (read: `sc_operator`,
  `sc_finance`, `sc_bod`; write: `sc_operator`, `sc_finance`, phía vận hành của SoD).
- Policy `supply_chain_bm04_schema@1.0.0` (trung tính) + `PUT /bm04-schema`
  (`action_duties.write`), schema không khai được trường giá hay cột có kiểu.
- `domain/commercial.py` (một chủ của `PRICE_FIELDS`, tổng do code tính, so tài khoản),
  `application/commercial.py` (làm rỗng giá ngay ở handler khi thiếu scope, lưu BM04 không
  scope giá thì mang giá cũ theo), `adapters/persistence/commercial_repository.py`,
  `presentation/commercial_routes.py` (`{value: null, redacted: true}`),
  `presentation/zalo_views.py` (một chủ danh sách trường Zalo).
- Web: `Bm04ProfileCard` (antd Form theo schema của tenant) trên hồ sơ phát triển,
  `POCommercialCard` trên Hồ sơ PO, khóa kèm lý do; `MaskedValue` trong `@dw/ui`;
  `formatPrice` trong `lib/money.ts`, `calendarDay` trong `lib/dates.ts`.
- Quyết định tạm ghi ở ADR 0026 "Sửa đổi 2026-10-09 (lát AI-01)".

Mutation (mỗi guard bỏ đi thì test đỏ; control xanh trước): giá BM04 hiện khi thiếu
`commercial.read`; đặt giá không cần `commercial.write`; lưu không giá làm mất giá cũ; giá PO
hiện khi thiếu scope; giá cho SKU không thuộc PO; thanh toán trích chứng từ sai loại; tài
khoản đọc khi thiếu scope; schema BM04 khai trường giá; view trả "0" thay vì redacted; view
Zalo thêm `unit_price`; prompt thêm biến `account_number`; web: ô giá vẽ số khi redacted;
web: lưu BM04 gửi giá khi không có quyền. 13/13 đỏ.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_commercial.py` (xuyên tenant và
workspace cho bốn bảng, phiên bản, FK chứng từ của chính PO, CHECK bằng tập domain, giá cần
tiền tệ), `dw_platform/tests/integration/test_privileges.py::test_commercial_tables_are_append_only_and_a_line_price_is_the_one_new_update`,
`test_role_catalogue.py` (thêm `COMMERCIAL_WRITE` vào phía vận hành), `test_rls_coverage.py`
trên bốn bảng mới, và migration chạy thật (`alembic upgrade head` rồi downgrade).

Chưa làm, để ticket sau: giao diện ghi thanh toán (AI-15), giao diện người liên hệ và tài
khoản NCC (ON-01 nạp, AI-07 dùng).
