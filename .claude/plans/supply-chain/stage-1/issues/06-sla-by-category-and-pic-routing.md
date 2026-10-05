# 06 — SLA theo Category, mốc giai đoạn 1, follow-up tới PIC, đổi PIC

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md
Area: supply-chain

## Mục tiêu

SLA đọc theo Category của hồ sơ; hai mốc `bm04`, `supplier_confirmation` có người đọc;
quá hạn báo cả PIC; PIC đổi được có lý do
([ADR 0019](../../../../../docs/adr/0019-e9-sla-policy-keyed-by-category.md)).

## Việc cần làm

1. **Policy** `supply_chain_sla@2.0.0.yaml`: `categories` (khóa, nhãn) của tenant,
   `default`, `by_category`. Migration dữ liệu nâng override 1.2.0 đang có lên 2.0.0
   (như `89e86dfabad6` đã làm), có test.
2. **Category đóng dấu:** `propose` từ chối Category không có trong danh sách của tenant;
   Hồ sơ PO nhận Category lúc ĐẶT HÀNG; `CreatePOCase` nhận Category tùy chọn.
3. **Bộ đánh giá** nhận Category; mốc mới gắn trạng thái: `bm04` →
   `profile_in_progress`, `supplier_confirmation` → `supplier_confirmation`, cùng
   `sample_collection` → `sample_requested`, `sample_testing` → `sample_testing`. Mốc
   `pending` vẫn coi như vắng.
4. **Follow-up cho hồ sơ phát triển:** `follow_ups` thêm `product_dev_case_id` với CHECK
   đúng một FK; lane sweep đọc cả hai loại hồ sơ, theo từng tenant.
5. **Người nhận `pic`:** `supply_chain_follow_ups` phiên bản mới thêm loại `pic`, đóng
   dấu lên follow-up lúc mở. Loại `pic` chỉ áp khi `pic_user_id` khác NULL **và** người
   đó còn membership trong workspace của hồ sơ lúc mở; nếu không, chỉ người nhận theo
   scope (Hồ sơ PO cũ và hồ sơ tạo trước S5 có `pic_user_id` NULL). Người đã rời
   workspace không nhận tin về hồ sơ của nó, kể cả qua kênh (ADR 0013,
   `ui-quality.md` mục 6).
6. **`reassign_pic`** trên cả hai aggregate (duty `supply_lead`, bắt buộc lý do, audit);
   follow-up đã mở giữ người nhận đã đóng dấu. Trên hồ sơ phát triển, `reassign_pic` bị
   từ chối ở trạng thái kết thúc (`ordered`, `cancelled`): sau ĐẶT HÀNG, PIC sống ở Hồ
   sơ PO, và đổi PIC là đổi ở Hồ sơ PO. Người mới phải có membership trong workspace.
7. **Override của tenant Elmich:** một script cục bộ ghi override qua
   `PUT /sla-policy` (không phải migration) với số của Elmich (BM04 4, chốt SP 5, đặt
   cọc 10, về cảng 21, thanh toán 10, nhập kho 5 ngày), mọi mốc
   `pending_business_confirmation`.
8. **Web:** chọn Category khi đề xuất; huy hiệu SLA trên hồ sơ phát triển.

## Tiêu chí chấp nhận

- [ ] Hồ sơ Category A dùng số của A; Category không có mốc dùng `default`; Category lạ
      bị từ chối lúc `propose`.
- [ ] Hồ sơ ở `profile_in_progress` quá số ngày `bm04` (đã xác nhận) mở follow-up
      `sla_breach`; mốc `pending` không mở gì. Mutation: bỏ gắn `bm04` thì test đỏ.
- [ ] PIC nhận follow-up; sau `reassign_pic`, follow-up mới tới PIC mới, follow-up cũ giữ
      người nhận cũ.
- [ ] `pic_user_id` NULL: follow-up chỉ tới người nhận theo scope. PIC đã mất membership
      của workspace: không nhận, scope vẫn nhận. Mỗi trường hợp một test.
- [ ] Đổi PIC của Hồ sơ PO sau ĐẶT HÀNG không đổi PIC lưu trên hồ sơ phát triển, và
      ngược lại `reassign_pic` trên hồ sơ phát triển `ordered` bị từ chối (chuyển từ S5).
- [ ] **Test âm xuyên workspace:** follow-up có `product_dev_case_id` của workspace W1
      không đọc, không đóng được từ workspace W2 cùng tenant.
- [ ] **Test âm xuyên tenant:** override của tenant B không đổi SLA của A; sweep của tenant
      A không đọc hồ sơ của B; `follow_ups` mới vẫn qua `test_rls_coverage.py`.
- [ ] Nâng override 1.2.0 → 2.0.0 giữ nguyên mọi số đã có.
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3 (quy tắc PIC), 3.2 đoạn dưới bảng; mục 5 điểm
  1, 2.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 "SLA", mục 3 "SLA" và "PIC rule".
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, migration `89e86dfabad6` (mẫu nâng override).

## Comments

- Đồng hồ vẫn chạy từ lúc vào trạng thái và trả `not_applicable` khi bị ngắt, chờ
  QE-14. Người được đổi PIC là TP Cung ứng, chờ QE-18.
