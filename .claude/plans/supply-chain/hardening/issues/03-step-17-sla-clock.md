# 03 — SLA bước 17 đo từ thanh toán tới lúc hàng vào kho

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

`warehouse_receipt` chỉ gắn `payment_completed`, nên đo từ thanh toán tới lúc *bắt đầu*
nhập kho; vào `warehouse_receiving` thì mốc biến mất (not_applicable) và việc nhập kho
kéo dài không ai thấy. QE-14 (tạm): đồng hồ bắt đầu lúc thanh toán xong, dừng khi hàng
vào kho.

## Tiêu chí chấp nhận

- [x] `warehouse_receiving` có mốc `warehouse_receipt`, và đồng hồ của nó là đồng hồ bắt
      đầu lúc vào `payment_completed`; `completed` dừng đồng hồ (trạng thái kết thúc).
- [x] Một chủ: `SLA_CLOCK_STARTS_IN` / `sla_clock_start_state` trong domain; câu SQL của
      `bulk_sla_clock_started_at` dựng CASE từ chính bảng đó.
- [x] Test: unit (mốc, trạng thái bắt đầu), integration DB thật (vào nhập kho không đổi
      thời điểm bắt đầu; workspace khác hỏi cùng id thì không có gì); mutation đỏ.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- Episode follow-up `warehouse_receipt@<lúc bắt đầu>` giữ nguyên khi hồ sơ sang
  `warehouse_receiving`, nên không mở follow-up thứ hai cho cùng một lần trễ.
- Tạm dừng trong lúc nhập kho rồi tiếp tục không khởi động lại đồng hồ; tạm dừng lúc
  `payment_completed` thì tính lại như mọi mốc (ADR 0019 mục 6). Cả hai chờ QE-14.
- Mô tả chữ trong `supply_chain_sla@2.0.0.yaml` đã cũ; không sửa file đã phát hành
  (checksum trong manifest), bản policy kế tiếp sửa. ADR 0019 sửa đổi 2026-10-08.
- Mutation: bỏ `WAREHOUSE_RECEIVING: PAYMENT_COMPLETED` → integration
  `test_receiving_goods_keeps_the_receipt_clock_that_started_at_payment` đỏ; bỏ mốc của
  `WAREHOUSE_RECEIVING` → unit `test_each_mapped_state_names_its_own_milestone` đỏ.
