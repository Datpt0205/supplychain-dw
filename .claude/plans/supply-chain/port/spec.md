# Chuyển `dw_supply_chain` từ nhánh lưu trữ sang nền tảng hiện tại (lát P)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Context Supply Chain được dựng trên nhánh `supply-chain` của repo nền tảng tới
28/9/2026, rồi gỡ khỏi nền tảng. Bản đầy đủ nằm ở nhánh `archive-supply-chain` và tag
`archive/supply-chain-2026-09-28` (commit `5d24c25`). Lát này đưa nó lên `main` của
repo sản phẩm (nền tảng `bf553f4`) như nó vốn có, để bước 10–17 chạy lại trước khi
dựng bước 1–9.

## Mục tiêu

`dw_supply_chain` cùng migration, config, eval, wiring API và worker, trang web của nó
chạy trên `main` với mọi cổng CI xanh, không đổi hành vi.

## Hiện trạng (kiểm ngày 5/10/2026)

- Mọi import nền tảng mà package dùng đều có trên `main` (khảo sát `docs/products/elmich/surveys/2026-10-05-archive-port.md`).
- 13 migration của context xen giữa migration nền tảng trong nhánh lưu trữ; trên
  `main` mọi migration nền tảng mà chúng cần đã nằm dưới head `855ae928c3fa`, nên chúng
  nối thành một chuỗi thẳng trên head đó.
- `test_rls_coverage.py` và `test_privileges.py` trên `main` đọc catalog, còn nhánh
  lưu trữ kiểm theo danh sách schema viết tay.
- Trang web của context viết trước quyết định antd (28/9/2026), dùng `@dw/ui` và lucide.

## Trong phạm vi

Tám bước của ticket 01. Trang web chuyển nguyên trạng; dựng lại bằng antd là
`antd-pages/issues/01`.

## Ngoài phạm vi

- Mọi thay đổi hành vi, kể cả các giới hạn đã ghi trong "Open" của area file.
- Chạy chuỗi migration mới trên database đã từng chạy chuỗi cũ (QO-1).

## Tiêu chí xong

Ticket 01 `resolved`; `make ci` xanh; integration của `dw_supply_chain`,
`test_rls_coverage.py`, `test_privileges.py` xanh.

## Danh sách ticket

| #   | Ticket                                                                                        | Status          | Blocked by |
| --- | --------------------------------------------------------------------------------------------- | --------------- | ---------- |
| 01  | [Chuyển package, migration, config, wiring](issues/01-port-dw-supply-chain.md)                | resolved        | —          |
| 02  | [Audit và trần chi tiêu cho ba lệnh ghi](issues/02-audit-and-spend-on-supply-chain-writes.md) | ready-for-agent | 01         |
| 03  | [Hạn giữ cho `follow_ups`](issues/03-follow-ups-retention.md)                                 | ready-for-agent | 01         |
