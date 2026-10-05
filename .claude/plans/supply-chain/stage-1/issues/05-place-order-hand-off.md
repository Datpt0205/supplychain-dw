# 05 — ĐẶT HÀNG: bàn giao bước 9 → 10, `order_requested`, `create_po`

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/04-item-code-sku-signoff-step-9.md
Area: supply-chain

## Mục tiêu

Nút ĐẶT HÀNG tạo đúng một Hồ sơ PO chờ tạo PO, mang PIC, Category, SKU của sản phẩm;
bước 10 đặt số PO và loại đơn; từ đó bước 11–17 chạy như cũ. 17 bước thành một luồng
([ADR 0017](../../../../../docs/adr/0017-e7-hand-off-via-order-requested.md)).

## Việc cần làm

1. **Migration `po_cases`:** `po_reference` nullable với CHECK (khác NULL trừ
   `order_requested`); cột `product_dev_case_id` (FK `RESTRICT`, index), `order_kind`
   (CHECK `new | reorder`), `pic_user_id`, `category`. Bảng `po_case_lines`
   (`po_case_id` FK `CASCADE`, `sku_id` FK `RESTRICT`, cả hai có index;
   `quantity > 0`), RLS FORCE, grant. Dòng có sẵn: `order_kind` điền `reorder` (ghi lý
   do trong docstring migration).
2. **`CaseState.ORDER_REQUESTED`** đứng đầu luồng chính; `CaseAction.CREATE_PO` (duty
   `ordering`) đặt `po_reference`, `order_kind`, → `po_created`. Nhãn "Chờ tạo PO" vào
   `CASE_STATE_LABEL` và glossary. `CREATE_PO` có tham số nên **không đi qua
   `apply_action`**: một lệnh riêng `CreatePO(po_reference, order_kind)` gọi
   `POCase.create_po(...)`; `apply_action` từ chối `CREATE_PO` có tên, và một tập
   `_COMMAND_ONLY_ACTIONS` cạnh `_NO_REASON_ACTIONS` và `_REASON_ACTIONS`
   (`po_case.py:290`, `:306`) giữ tính đủ: mỗi `CaseAction` nằm đúng một trong ba tập (unit test).
3. **Bảng duty có phiên bản mới:** `supply_chain_action_duties@1.1.0.yaml` thêm
   `create_po: ordering`. `SupplyChainActionDuties._every_action_has_a_duty`
   (`action_duties.py:60-68`) từ chối tài liệu thiếu một `CaseAction`, nên mọi override
   đã lưu trong `platform.policy_overrides` sẽ hỏng và mọi `AdvancePOCase` của tenant đó
   lỗi. Một migration dữ liệu (mẫu `89e86dfabad6`) thêm `create_po: ordering` vào mọi
   override `supply_chain_action_duties` đã lưu, cùng thay đổi.
4. **`PlaceOrder`** (duty `ordering`), một giao dịch. Trước tiên câu cập nhật có điều
   kiện của ADR 0017 (`ready_to_order` → `ordered`, trạng thái mới, kết thúc; khớp cả
   `state` và `version` đã đọc); 0 dòng thì 409 và không chèn gì; rồi
   chèn `po_cases` (`order_requested`, chép PIC, Category, NCC từ chính dòng hồ sơ phát
   triển đọc trong giao dịch này) và `po_case_lines` (SKU, `planned_quantity` làm số
   lượng ban đầu, sửa được tới `create_po`); thông báo cho người giữ duty `ordering`.
   Câu cập nhật có điều kiện là thứ bảo đảm một PO, không phải UNIQUE (ADR 0017).
   Không ghi sự kiện outbox: chưa có consumer nào (failure-modes #1); thêm khi có
   consumer, với schema sự kiện có phiên bản.
5. **`create_po`** báo người giữ duty `finance` (Kế toán), theo cột "Bàn giao cho" của
   bước 10.
6. **`CreatePOCase`** (không qua giai đoạn 1) nhận `order_kind` bắt buộc,
   `product_dev_case_id` NULL, và đóng dấu `pic_user_id = context.principal_id`. Dòng có
   sẵn trước migration để `pic_user_id` NULL (S6 nói người nhận khi NULL).
7. **Mọi chỗ đọc `po_reference` chịu được NULL:** aggregate và repository, `ListPOCases`
   và bộ lọc, `case_query` (không bao giờ khớp NULL), daily brief và bộ kiểm câu tóm tắt,
   prompt `delay_impact_analysis`, contracts TypeScript (`string | null`), trang danh
   sách và chi tiết ("Chưa có số PO"), fixture và expected của eval.
8. **`order_requested` ở các chỗ đọc theo trạng thái**, mỗi chỗ một unit test:
    - `missing_update.py:74` coi mọi trạng thái không kết thúc là tới hạn nhắc cập nhật
      NCC; `order_requested` bị loại (chưa có PO thì chưa có NCC để hỏi);
    - đánh giá SLA trả `not_applicable` ở `order_requested` (mốc riêng là việc sau, khi
      Elmich trả lời QE-01);
    - daily brief và hàng chú ý: hồ sơ `order_requested` hiện thành nhóm "Chờ tạo PO"
      cho người giữ duty `ordering`, không vào nhóm thiếu cập nhật;
    - ma trận approval mặc định không liệt kê `create_po`.
9. **Test đầu-cuối** (integration, qua handler): một sản phẩm đi bước 1 → `ordered` →
   `create_po` → bước 11–17 → `completed`, với approval quyết bởi người dùng thử đúng vai.

## Tiêu chí chấp nhận

- [ ] Hai `PlaceOrder` song song cho cùng hồ sơ (hai giao dịch thật trên Postgres): một
      Hồ sơ PO; lần hai 409. Mutation: bỏ `AND version = :v AND state = 'ready_to_order'`
      khỏi câu cập nhật thì test đỏ (ghi vào Comments).
- [ ] Chèn thẳng `po_cases` ở `po_created` với `po_reference` NULL (vai `dw_app`) bị
      CHECK từ chối.
- [ ] PIC là dấu: sau `PlaceOrder`, `po_cases.pic_user_id` bằng `pic_user_id` của dòng
      hồ sơ phát triển đọc trong cùng giao dịch; không chỗ đọc nào của Hồ sơ PO join lại
      `product_dev_cases` để lấy PIC (test hoặc grep trong CI). Đổi PIC sau ĐẶT HÀNG được
      kiểm ở S6, nơi `reassign_pic` ra đời.
- [ ] Một override `supply_chain_action_duties` lưu trước migration vẫn nạp được sau
      migration và có `create_po: ordering`; `AdvancePOCase` của tenant đó chạy.
- [ ] `apply_action(CREATE_PO)` bị từ chối có tên; unit test tính đủ của ba tập hành
      động đỏ khi thêm một `CaseAction` mà không xếp vào tập nào.
- [ ] `CreatePOCase` đóng dấu `pic_user_id` là người gọi.
- [ ] **Test âm:** `PlaceOrder` trên hồ sơ tenant khác hoặc workspace khác trả 404; RLS
      của `po_case_lines` chặn tenant B; `test_rls_coverage.py` xanh.
- [ ] Unit cho từng chỗ đọc ở bước 6 với `po_reference` NULL; eval dataset hiện có vẫn
      xanh.
- [ ] Test đầu-cuối ở bước 7 xanh.
- [ ] Mutation: bỏ CHECK trong migration thì test chèn thẳng đỏ (ghi vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 9–10; mục 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3, "Hand-off at ĐẶT HÀNG"; mục 2 hàng 10.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "Hand-off".

## Comments

- Một hồ sơ phát triển sinh mấy PO, và Hàng mới có được tạo không qua giai đoạn 1, chờ
  QE-12; thiết kế không chặn trường hợp nào.
