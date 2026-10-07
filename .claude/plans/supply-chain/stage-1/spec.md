# Giai đoạn 1: Hồ sơ phát triển sản phẩm, bước 1–9, nối vào Hồ sơ PO (lát S1–S7)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Riêng của context. Đặc tả nghiệp vụ: `docs/products/elmich/process.md` mục 3 và 4.
Quyết định: [ADR 0016](../../../../docs/adr/0016-e6-stage-1-is-a-product-development-case-inside-dw-supply-chain.md)
(aggregate riêng, trạng thái, duty),
[ADR 0017](../../../../docs/adr/0017-e7-hand-off-via-order-requested.md) (bàn giao ĐẶT HÀNG),
[ADR 0018](../../../../docs/adr/0018-e8-item-code-and-sku-uniqueness-owned-by-the-database.md)
(mã hàng, SKU), [ADR 0019](../../../../docs/adr/0019-e9-sla-policy-keyed-by-category.md)
(SLA theo Category), [ADR 0020](../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md)
(người quyết), [ADR 0021](../../../../docs/adr/0021-e11-case-documents-through-an-object-storage-port.md)
(chứng từ). Từ ngữ theo `packages/python/dw_supply_chain/CONTEXT.md`.

## Mục tiêu

17 bước của Elmich chạy thành một luồng: một sản phẩm được đề xuất ở bước 1, đi qua
lấy mẫu, test, BGĐ duyệt, BM04, thống nhất với NCC, mã hàng, SKU, trình ký, rồi nút
ĐẶT HÀNG tạo Hồ sơ PO đi tiếp bước 10–17 đã có. PIC đóng dấu ở bước 1 theo suốt luồng.

## Hiện trạng (kiểm ngày 5/10/2026)

- Bước 1–9 không có code nào (`docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2).
- Bước 10–17 có ở `POCase` sau lát port; `po_reference` bắt buộc khác rỗng; không có
  PIC, Category, loại đơn, SKU.
- Hai mốc SLA `bm04`, `supplier_confirmation` có trong policy mà không ai đọc.
- Approval chưa giới hạn người quyết (lát A); chưa có chứng từ (lát D).

## Trong phạm vi

Ticket 01–07, theo thứ tự phụ thuộc. Mỗi ticket là một lát dọc: domain, migration,
handler, route, trang antd tối thiểu, test.

## Ngoài phạm vi

- Bước 12 (sơ đồ con thiết kế màu và bao bì) và phần MKT; Phần B.
- Đồng bộ ERP; nhắc NCC qua Zalo.
- Mức độ ưu tiên ở bước 1: thang chưa rõ (QE-13).

## Giả định chờ Elmich

Ghi ở ADR 0016 và từng ticket; mỗi giả định trỏ một câu QE trong area file. Elmich trả
lời khác thì sửa ticket trước khi làm, hoặc mở ticket sửa nếu đã làm.

## Tiêu chí xong

Bảy ticket `resolved`; test đầu-cuối của ticket 05 chạy một sản phẩm từ bước 1 tới
`completed` của Hồ sơ PO; eval smoke có các ca an ninh của ticket 07.

Trạng thái (7/10/2026): **đạt.** Ticket 01–07 `resolved`; test đầu-cuối của 05 xanh;
eval smoke `supply_chain@1.4.0` 43/43 với 11 ca giai đoạn 1 (S7), mỗi ca an ninh đỏ khi gỡ
lớp chặn. Daily brief, tóm tắt brief và command bar cho hồ sơ phát triển tách sang ticket
08 (tính năng mới, việc brief chờ QE-19), ngoài bảy ticket của tiêu chí này.

## Danh sách ticket

| #   | Ticket                                                                                        | Status          | Blocked by                                                                                                                                            |
| --- | --------------------------------------------------------------------------------------------- | --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Hồ sơ phát triển, bước 1–5](issues/01-product-case-steps-1-5.md)                             | resolved        | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/case-documents/issues/01-case-documents.md              |
| 02  | [BGĐ duyệt mẫu, bước 6](issues/02-bod-review-step-6.md)                                       | resolved        | .claude/plans/supply-chain/stage-1/issues/01-product-case-steps-1-5.md, .claude/plans/supply-chain/approval-decider-scope/issues/01-required-scope.md |
| 03  | [BM04 và thống nhất với NCC, bước 7–8](issues/03-bm04-and-supplier-confirmation-steps-7-8.md) | resolved        | .claude/plans/supply-chain/stage-1/issues/02-bod-review-step-6.md                                                                                     |
| 04  | [Mã hàng, SKU, trình ký, bước 9](issues/04-item-code-sku-signoff-step-9.md)                   | resolved        | .claude/plans/supply-chain/stage-1/issues/03-bm04-and-supplier-confirmation-steps-7-8.md                                                              |
| 05  | [ĐẶT HÀNG: bàn giao 9 → 10](issues/05-place-order-hand-off.md)                                | resolved        | .claude/plans/supply-chain/stage-1/issues/04-item-code-sku-signoff-step-9.md                                                                          |
| 06  | [SLA theo Category, thông báo cho PIC](issues/06-sla-by-category-and-pic-routing.md)          | resolved        | .claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md                                                                                  |
| 07  | [Eval giai đoạn 1: injection, xuyên tenant, thiếu bằng chứng](issues/07-stage-1-evals.md)     | resolved        | .claude/plans/supply-chain/stage-1/issues/06-sla-by-category-and-pic-routing.md                                                                       |
| 08  | [Giai đoạn 1 trong daily brief và command bar](issues/08-stage-1-brief-and-command-bar.md)    | ready-for-agent | .claude/plans/supply-chain/stage-1/issues/07-stage-1-evals.md; QE-19 (việc 1)                                                                         |
