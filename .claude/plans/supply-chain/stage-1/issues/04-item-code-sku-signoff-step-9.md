# 04 — Mã hàng chính thức, SKU, trình ký BGĐ và Kế toán (bước 9)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/03-bm04-and-supplier-confirmation-steps-7-8.md
Area: supply-chain

## Mục tiêu

Cung ứng tạo mã hàng chính thức và các SKU, không trùng trong công ty; hồ sơ được ký
theo thứ tự tenant cấu hình; ký đủ thì sẵn sàng đặt hàng
([ADR 0018](../../../../../docs/adr/0018-e8-item-code-and-sku-uniqueness-owned-by-the-database.md)).

## Việc cần làm

1. **Migration:** `supply_chain.item_codes` (UNIQUE `(tenant_id, code)`, UNIQUE
   `(tenant_id, product_dev_case_id)`, FK hồ sơ `RESTRICT`), `supply_chain.skus`
   (UNIQUE `(tenant_id, sku_code)`, `item_code_id` FK NOT NULL `RESTRICT` có index,
   `variant_label`, `planned_quantity` NULL hoặc `> 0`). Mã cắt khoảng trắng, CHECK
   khác rỗng. RLS FORCE, grant.
2. **Hành động** (duty `ordering`): `issue_item_code`, `add_sku`, `remove_sku` (chỉ khi
   chưa trình ký), `submit_for_signoff` (`item_coding` → `pending_signoff`; cần mã hàng
   và ít nhất một SKU).
3. **Adapter** đổi vi phạm UNIQUE thành `ConflictError` theo tên ràng buộc, nói mã nào
   trùng.
4. **Trình ký:** policy `supply_chain_product_approvals@1.0.0` thêm danh sách
   `signoff` có thứ tự: bước `bod` (`required_scope: supply_chain.approve.bod`) rồi bước
   `accounting` (`required_scope: supply_chain.approve.accounting`). Graph của ticket 02
   chạy các bước theo thứ tự, mỗi bước một approval
   `supply_chain.product_action.signoff` mang `required_scope` và `step`. Mọi bước
   duyệt → graph áp `signoff_approve` (→ `ready_to_order`); một bước không duyệt → graph
   áp `signoff_reject`* (→ `item_coding`, nhận xét là lý do). Hai hành động và
   `remove_sku` có hàng trong bảng của ADR 0016.
5. **Vai:** scope `supply_chain.approve.accounting` cấp cho vai `sc_finance` có sẵn (Kế
   toán, duty `finance`, bước 11 và 16) bằng migration `platform.roles`; không thêm
   `sc_accounting` trừ khi Elmich tách hai người ở QE-16 (glossary, mục "Kế toán").
6. **Trạng thái mới** `pending_signoff`, `ready_to_order`.
7. **Web:** khối mã hàng và SKU (thêm, bỏ, lỗi trùng hiện ngay tại trường), tiến độ ký.

## Tiêu chí chấp nhận

- [ ] Hai giao dịch song song tạo cùng mã: một thành công, một 409 (test integration).
- [ ] `add_sku` trước `issue_item_code` bị từ chối ở aggregate; chèn thẳng SKU không mã
      hàng bị database từ chối.
- [ ] Cùng mã hàng, cùng mã SKU ở tenant khác: được (độc lập theo tenant).
- [ ] **Test âm RLS** cho `item_codes`, `skus`; **test âm route** xuyên tenant, workspace.
- [ ] Trình ký: người chỉ có `supply_chain.approve.accounting` không quyết được bước BGĐ;
      thứ tự theo policy; override của tenant đổi thứ tự thì graph theo thứ tự mới cho
      hồ sơ trình sau.
- [ ] Không duyệt ở bước Kế toán: hồ sơ về `item_coding`, mã hàng và SKU giữ nguyên.
- [ ] Mutation: bỏ ánh xạ tên ràng buộc thì lỗi trùng ra 500 và test đỏ (ghi vào
      Comments).
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 9; mục 5 điểm 2, 3.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3 (`ItemCode`, `Sku`, hàng `ITEM_CODING`, `PENDING_SIGNOFF`).
- `.claude/rules/code-quality.md`, "Single responsibility" (mẫu đổi lỗi ràng buộc của
  tách nhiệm).

## Comments

- Giả định: ký tuần tự BGĐ rồi Kế toán, cả hai bắt buộc, không ngưỡng giá trị (QE-10);
  thứ tự là policy nên đổi không cần code.
- "Chốt số lượng SKU" chưa rõ nghĩa (QE-11); `planned_quantity` để NULL được.
