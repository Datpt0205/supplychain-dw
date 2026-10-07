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

## Sửa đổi 2026-10-07 (tạm, lát S5; Đạt ủy quyền quyết các điểm mở)

Làm theo quyết định ở trên. Các điểm mở được quyết tạm theo cách an toàn nhất; chi tiết
và test ở Comments của `.claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md`.

1. **QE-12 (tạm): một hồ sơ phát triển, một Hồ sơ PO, và database cũng nói vậy.** Lệch
   chữ "không có UNIQUE" ở trên: thêm `uq_po_cases_tenant_id_workspace_id_product_dev_case_id`
   (cũng là index của FK). Câu cập nhật có điều kiện vẫn là thứ bảo đảm (có test và
   mutation riêng); UNIQUE là câu trả lời thứ hai của database, như ADR 0018 cho mã hàng,
   để một đường ghi khác (sau này, hoặc ghi thẳng) không tạo PO thứ hai. Elmich trả lời
   "nhiều" thì bỏ ràng buộc bằng một migration mới và đổi máy trạng thái. Hàng đặt lại
   không qua giai đoạn 1: `CreatePOCase` như hôm nay, `order_kind` bắt buộc, không có
   `product_dev_case_id`. Một PO gộp SKU của nhiều sản phẩm: chưa làm.
2. **`po_reference`** do Cung ứng đặt ở `create_po` (duty `ordering` trong policy duty PO
   1.1.0, tenant ghi đè được). CHECK `ck_po_cases_po_reference`: NULL chỉ ở
   `order_requested`, hoặc `cancelled` từ đó; khác rỗng ở mọi chỗ khác. Không số tạm.
3. **Ở `order_requested` chỉ có `create_po` hoặc `cancel`.** Không tạm dừng: chưa có PO
   thì chưa có gì bên ngoài để chờ. SLA `not_applicable`; không bao giờ tới hạn nhắc NCC;
   daily brief đưa hồ sơ vào nhóm `waiting_on_us` với qualifier `order_requested` ("Chờ
   tạo PO"), không thêm tín hiệu mới (thêm thì mọi override `supply_chain_brief` đã lưu sẽ
   hỏng). Nhóm hiện cho mọi người đọc được brief như mọi nhóm khác; người giữ duty của
   `create_po` được báo bằng thông báo, không phải bằng việc chỉ họ thấy nhóm.
4. **Dòng PO** (`po_case_lines`): `quantity` NULL hoặc > 0, vì `planned_quantity` của
   SKU có thể để trống (QE-11); `create_po` nhận số lượng cho dòng còn trống hoặc cần sửa
   và từ chối (409, nêu SKU) khi còn dòng trống. Hẹp theo tenant VÀ workspace; FK tới Hồ
   sơ PO `CASCADE`, tới SKU `RESTRICT`, cả hai ghép với workspace. `dw_app`: SELECT,
   INSERT, UPDATE `quantity`; không DELETE (dòng đi cùng Hồ sơ PO, kể cả khi offboarding).
   **Mục mở:** `po_cases` vẫn chỉ hẹp theo tenant (từ `fddd7579ba27`), nên người của
   workspace khác cùng tenant thấy Hồ sơ PO mà không thấy dòng của nó.
5. **PIC, Category, NCC là dấu** chép từ dòng hồ sơ phát triển đọc trong giao dịch ĐẶT
   HÀNG; repository Hồ sơ PO không nhắc tới `product_dev_cases` (test). `CreatePOCase`
   đóng dấu người gọi. Không lệnh nào có tham số PIC; route ĐẶT HÀNG nhận body rỗng hoặc
   không body, mọi trường (PIC, Category, NCC) là 422.
6. **Thông báo**, sau khi ghi xong (hỏng thì ghi log, việc đã ghi vẫn còn): ĐẶT HÀNG báo
   người giữ duty mà policy PO của tenant giao cho `create_po` (một chủ), trừ người bấm;
   `create_po` báo người giữ duty `finance` (Kế toán), trừ người làm.
7. **Audit:** ĐẶT HÀNG ghi hai sự kiện (`supply_chain.product_case.place_order`,
   `supply_chain.po_case.order_requested`) trong giao dịch ghi hai dòng; `create_po` ghi
   `supply_chain.po_case.create_po` trong giao dịch của bước. Các bước 11–17 vẫn chưa ghi
   audit (có từ trước, ngoài lát này).
8. **Bấm hai lần:** cùng `Idempotency-Key` trả lại câu trả lời đầu; khóa khác là 409
   (câu cập nhật có điều kiện), không chèn gì. Số PO đã có trong tenant là 409 nêu ràng
   buộc và số PO (cả `CreatePOCase`, trước đây là 500).
9. **Policy:** duty hồ sơ phát triển 1.3.0 thêm `place_order: ordering` (override cũ lấy
   duty nền tảng cho bước này, `STEPS_ADDED_AFTER`); duty PO 1.1.0 thêm `create_po:
ordering` và migration dữ liệu thêm khóa này vào mọi override đã lưu; ma trận approval
   từ chối `create_po` (không ai đọc nó: `create_po` là lệnh riêng, không khởi động run).
10. **Migration** `84d1c1946b44` (id ngẫu nhiên, down_revision `76bd1b5fc546`, một head).
    Hồ sơ PO có sẵn được điền `order_kind = 'reorder'` (đều do `CreatePOCase` mở, không qua
    giai đoạn 1), rồi bỏ default. Downgrade từ chối khi còn dòng cần phần nó gỡ.

## Sửa đổi 2026-10-07 (lát P4; Đạt ủy quyền, chọn hướng đóng an toàn)

Đóng **Mục mở** của điểm 4 ở trên. Chi tiết và test ở Comments của
`.claude/plans/supply-chain/port/issues/04-po-cases-narrowed-by-workspace.md`.

1. **Hồ sơ PO chỉ đọc được trong workspace của nó**, như hồ sơ phát triển (ADR 0016),
   dòng PO và chứng từ (ADR 0021). Phòng ban cần thấy nhiều workspace thì được thêm làm
   thành viên của từng workspace; không có quyền đọc cả tenant cho Hồ sơ PO.
2. **Migration `62cdcf3bf2d2`**: `po_cases`, `po_case_state_transitions`,
   `supplier_updates`, `delay_impact_analyses` (bốn bảng `supply_chain` cuối cùng còn chỉ
   theo tenant) theo đúng dạng của `CLAUDE.md`, USING và WITH CHECK. FK của con tới hồ sơ
   và của phân tích trễ tới cập nhật NCC ghép thêm `workspace_id` (cùng tên ràng buộc);
   `uq_supplier_updates_tenant_id_workspace_id_id` là đích mới; hai UNIQUE chỉ theo tenant
   không còn FK nào dùng nên bỏ. Index phân trang có `workspace_id` sau `tenant_id`.
3. **Số PO vẫn duy nhất trong tenant** (`uq_po_cases_tenant_id_po_reference`): số PO là
   của công ty. Người ở W2 gõ số W1 đã dùng nhận 409 "đã có trong công ty", chỉ biết số
   đã có, không thấy hồ sơ.
4. **Đường đọc không có người gọi:** lane follow-up đã đi từng workspace qua
   `workspaces_with_cases()` (S6); không cần hàm SECURITY DEFINER mới. Offboarding đọc và
   xóa mọi workspace bằng `app.workspace_scope = 'tenant'`. Control Tower, daily brief, SLA,
   thanh lệnh (`POST /case-query`) và câu hỏi Zalo đọc theo context của người gọi, nên hẹp
   theo workspace của họ.
5. **Downgrade** đưa bốn policy, các FK và index về dạng chỉ theo tenant, thêm lại hai
   UNIQUE; không mất dòng nào.
