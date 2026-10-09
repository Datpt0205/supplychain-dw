# ON-01 — Nạp NCC, danh mục mã hàng/SKU, người dùng (E16)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md
Area: supply-chain

## Mục tiêu

Dữ liệu Elmich đang có vào hệ thống một lần, qua đúng handler, có chạy thử.

## Việc cần làm

1. Mẫu Excel (`docs/products/elmich/import-template.xlsx`) bốn sheet; lệnh
   `scripts/import_supply_chain.py dry-run|apply <file>`; scope `supply_chain.import`.
2. NCC (tên, mã, liên hệ, tài khoản), bảng `catalogue_items` (mã hàng, SKU, tên, Category)
   mà kiểm trùng bước 9 đọc; người dùng qua provisioning + Keycloak.
3. Báo cáo từng dòng; idempotent theo mã ngoài; audit actor là người chạy.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Chạy thử không ghi gì (đếm dòng trước/sau).
- [ ] Chạy lại cùng file không tạo bản sao.
- [ ] Dòng xấu được báo, không làm hỏng dòng khác.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0027 (E16); HR4 (danh mục NCC).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
