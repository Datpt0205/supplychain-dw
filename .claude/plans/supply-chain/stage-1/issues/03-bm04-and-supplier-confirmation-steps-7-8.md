# 03 — Profile SP (BM04) và thống nhất SP với NCC (bước 7–8)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/02-bod-review-step-6.md
Area: supply-chain

## Mục tiêu

R&D và Cung ứng hoàn tất BM04; TP Cung ứng xác nhận SP đã thống nhất với NCC, kèm email
của NCC; hồ sơ sang tạo mã hàng.

## Việc cần làm

1. **Trạng thái mới** `supplier_confirmation`, `item_coding`. Hành động
   `complete_profile` (`profile_in_progress` → `supplier_confirmation`, duty `rnd`) và
   `confirm_with_supplier` (`supplier_confirmation` → `item_coding`, duty
   `supply_lead`).
2. **Chứng từ bắt buộc:** `complete_profile` cần `product_profile_bm04`;
   `confirm_with_supplier` cần `supplier_confirmation_email`; cả hai thuộc đúng hồ sơ.
3. **Duty và vai:** scope `supply_chain.duty.supply_lead`; vai `sc_supply_lead` (TP Cung
   ứng); policy duty gán hai hành động.
4. **Web:** hai hành động trên trang chi tiết, tải chứng từ ngay trong luồng.

## Tiêu chí chấp nhận

- [ ] Unit: chuyển đúng; sai trạng thái bị từ chối; thiếu chứng từ 409 nêu loại.
- [ ] Duty: `sc_rnd` không `confirm_with_supplier`; `sc_supply_lead` không
      `complete_profile` (403).
- [ ] **Test âm route:** hai hành động trên hồ sơ tenant khác hoặc workspace khác trả 404;
      chứng từ của tenant khác không thỏa điều kiện (không thể gắn, xem lát D).
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 7–8; mục 5 điểm 1.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2 hàng 7–8 ("SLA key only"), mục 3 hàng `PROFILE_IN_PROGRESS`,
  `SUPPLIER_CONFIRMATION`.

## Comments

- Giả định: bước 8 là xác nhận trong ứng dụng kèm file email, không đọc hộp thư (QE-09).
- SLA `bm04` và `supplier_confirmation` được gắn vào hai trạng thái ở ticket 06.
