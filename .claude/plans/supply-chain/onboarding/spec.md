# Nạp dữ liệu Elmich đang có (ON-01, ON-02)

Area: supply-chain · Nhánh: `feat/elmich-a-d-s1` · Viết: 9/10/2026

Quyết định: [ADR 0027 (E16)](../../../../packages/python/dw_supply_chain/docs/adr/0027-e16-one-time-import-of-existing-data.md).

## Mục tiêu

NCC, danh mục mã hàng và SKU, người dùng và hồ sơ đang chạy của Elmich vào hệ thống một lần
từ Excel, qua đúng handler, có chạy thử, để ngày dùng thật không phải nhập tay lại và kiểm
trùng mã ở bước 9 thấy cả mã đã có ngoài ứng dụng.

## Hiện trạng (kiểm ngày 9/10/2026)

- Không có lệnh nạp; `keycloak_dev_users.py` chỉ cho dev.
- Hồ sơ PO chỉ mở ở `po_created`, hồ sơ phát triển chỉ ở `proposed`.
- Danh mục NCC có (HR4), chưa có liên hệ, tài khoản, mã.

## Ngoài phạm vi

Tích hợp ERP; đồng bộ định kỳ.

## Danh sách ticket

| #     | Ticket                                                                         | Size | Status          | Blocked by       |
| ----- | ------------------------------------------------------------------------------ | ---- | --------------- | ---------------- |
| ON-01 | [Nạp NCC, danh mục, người dùng](issues/01-import-suppliers-catalogue-users.md) | M    | ready-for-agent | ai-automation 01 |
| ON-02 | [Nạp hồ sơ đang chạy ở trạng thái hiện tại](issues/02-import-open-cases.md)    | M    | ready-for-agent | ON-01            |
