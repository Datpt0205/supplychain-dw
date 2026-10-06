---
status: Accepted
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # bước 9, 10
    - ../../packages/python/dw_supply_chain/src/dw_supply_chain/domain/po_case.py # POCase, po_reference
    - ../../db/migrations/versions/fddd7579ba27_supply_chain_schema_po_cases.py
---

# E7. Bàn giao ĐẶT HÀNG tạo Hồ sơ PO ở trạng thái `order_requested`, số PO để trống tới bước 10

Bước 9 kết thúc bằng nút ĐẶT HÀNG; bước 10 mới tạo PO. Nhưng `POCase` hôm nay đòi
`po_reference` khác rỗng, mà số PO chưa có ở bước 9.

**Quyết định:**

- `PlaceOrder` chạy trong một giao dịch: Hồ sơ phát triển `ready_to_order` →
  `ordered`; chèn một dòng `po_cases` ở trạng thái mới **`order_requested`** mang
  `product_dev_case_id`, `pic_user_id` (chép từ hồ sơ phát triển, một dấu, không tra
  lại), `category`, `supplier_name`, `order_kind = 'new'` và các dòng PO dự kiến
  (SKU, số lượng); gửi thông báo cho người giữ duty `ordering` của workspace.
- **Một sản phẩm, một PO, do câu cập nhật có điều kiện bảo đảm.** Trong cùng giao
  dịch với lệnh chèn PO:

    ```sql
    UPDATE supply_chain.product_dev_cases
       SET state = 'ordered', version = version + 1
     WHERE id = :id AND state = 'ready_to_order' AND version = :v
    ```

    0 dòng nghĩa là đã có người đặt hàng hoặc hồ sơ đã đổi: trả 409 và không chèn gì. Hai giao
    dịch đồng thời cùng đọc `ready_to_order` thì giao dịch sau chờ khóa dòng, thấy
    `version` đã đổi và không cập nhật được.

- Không ghi sự kiện outbox: chưa có consumer nào đọc nó (failure-modes #1). Khi có
  consumer, sự kiện ra đời cùng nó, với schema có phiên bản (`CLAUDE.md`).
- Hành động `create_po` (bước 10, duty `ordering`) đặt `po_reference` và
  `order_kind`, chuyển `order_requested` → `po_created`, báo Kế toán (cột "Bàn giao
  cho" của bước 10).
- `po_reference` thành nullable với CHECK: khác NULL ở mọi trạng thái trừ
  `order_requested`.
- Cột mới của `po_cases`: `product_dev_case_id` (FK `ON DELETE RESTRICT`, có index,
  NULL với Hàng đặt lại không qua giai đoạn 1), `order_kind` (CHECK `new | reorder`),
  `pic_user_id`, `category`. Bảng mới `po_case_lines` (`sku_id` FK `RESTRICT`,
  `quantity > 0`).
- Hàng đặt lại không qua giai đoạn 1 vẫn tạo bằng `CreatePOCase` như hôm nay, với
  `order_kind = 'reorder'`, đi thẳng vào `po_created`.

## Phương án đã cân nhắc

- **Chỉ tạo Hồ sơ PO ở bước 10, bằng tay.** Bác: mất liên kết sản phẩm–PO, mất PIC,
  và không ai được báo rằng có hàng chờ tạo PO.
- **Sinh số PO tạm.** Bác: dữ liệu giả trông như thật trong danh sách, brief và câu
  hỏi.

## Hệ quả

- Mọi chỗ đọc `po_reference` phải chịu được NULL: aggregate, repository, danh sách
  và bộ lọc, daily brief, câu hỏi command bar, grader eval, trang web. Ticket S5 liệt
  kê từng chỗ.
- Bấm ĐẶT HÀNG hai lần không tạo hai PO: lần hai gặp hồ sơ đã `ordered`, câu cập nhật
  có điều kiện trả 0 dòng.
- Một sản phẩm sinh mấy PO là điểm mở (QE-12). Bảng cho phép nhiều PO trên một
  `product_dev_case_id` (không có UNIQUE); máy trạng thái chỉ cho một, vì `ordered` là
  trạng thái kết thúc. Elmich trả lời "nhiều" thì đổi máy trạng thái, không đổi bảng.
