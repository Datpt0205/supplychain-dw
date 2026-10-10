# 06 — SLA theo Category, mốc giai đoạn 1, follow-up tới PIC, đổi PIC

Status: resolved
Blocked by: .claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md
Area: supply-chain

## Mục tiêu

SLA đọc theo Category của hồ sơ; hai mốc `bm04`, `supplier_confirmation` có người đọc;
quá hạn báo cả PIC; PIC đổi được có lý do
([ADR 0019](../../../../../packages/python/dw_supply_chain/docs/adr/0019-e9-sla-policy-keyed-by-category.md)).

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

- [x] Hồ sơ Category A dùng số của A; Category không có mốc dùng `default`; Category lạ
      bị từ chối lúc `propose`.
- [x] Hồ sơ ở `profile_in_progress` quá số ngày `bm04` (đã xác nhận) mở follow-up
      `sla_breach`; mốc `pending` không mở gì. Mutation: bỏ gắn `bm04` thì test đỏ.
- [x] PIC nhận follow-up; sau `reassign_pic`, follow-up mới tới PIC mới, follow-up cũ giữ
      người nhận cũ.
- [x] `pic_user_id` NULL: follow-up chỉ tới người nhận theo scope. PIC đã mất membership
      của workspace: không nhận, scope vẫn nhận. Mỗi trường hợp một test.
- [x] Đổi PIC của Hồ sơ PO sau ĐẶT HÀNG không đổi PIC lưu trên hồ sơ phát triển, và
      ngược lại `reassign_pic` trên hồ sơ phát triển `ordered` bị từ chối (chuyển từ S5).
- [x] **Test âm xuyên workspace:** follow-up có `product_dev_case_id` của workspace W1
      không đọc, không đóng được từ workspace W2 cùng tenant.
- [x] **Test âm xuyên tenant:** override của tenant B không đổi SLA của A; sweep của tenant
      A không đọc hồ sơ của B; `follow_ups` mới vẫn qua `test_rls_coverage.py`.
- [x] Nâng override 1.2.0 → 2.0.0 giữ nguyên mọi số đã có.
- [x] `make ci` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3 (quy tắc PIC), 3.2 đoạn dưới bảng; mục 5 điểm
  1, 2.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 "SLA", mục 3 "SLA" và "PIC rule".
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, migration `89e86dfabad6` (mẫu nâng override).

## Comments

- Đồng hồ vẫn chạy từ lúc vào trạng thái và trả `not_applicable` khi bị ngắt, chờ
  QE-14. Người được đổi PIC là TP Cung ứng, chờ QE-18.

### S6, 2026-10-07 (lead; Đạt ủy quyền quyết các điểm mở)

Quyết định tạm, đủ ở ADR 0019 sửa đổi S6; tóm tắt:

- **Schema 2.0:** `categories` (khóa slug, nhãn), `default`, `by_category`; mốc riêng thắng
  nguyên khối (riêng còn `pending` thì không đánh giá). Nền 2.0.0: số thử 1–2 ngày, Category
  thử `noi`, `chao`; mốc mới `sample_collection`, `sample_testing`, `bod_review` (QE-01),
  `bm04`, `supplier_confirmation`, `item_coding` (QE-05), `signoff` (QE-01).
- **Category kiểm một chỗ** (`require_category`): `propose` (web, Zalo) và `CreatePOCase`
  (tùy chọn) chỉ nhận khóa của danh sách tenant, 422 nêu `category`. Hồ sơ trước S6 giữ chữ
  tự do, đánh giá theo `default`, web hiển thị nguyên chữ. `GET /product-categories`.
- **Zalo:** chữ đã kiểm nguyên văn giải theo khóa hoặc nhãn (bỏ hoa thường, dấu), đúng một
  mục; khác thì hỏi lại với tối đa 5 lựa chọn của tenant; "Đồng ý" trên Category đã rời danh
  sách không tạo hồ sơ. Eval `supply_chain@1.3.0` (32 ca, +2).
- **Override 1.x → 2.0:** migration `d56da3dd2146` (`UPGRADE_SLA_OVERRIDES`), giữ mọi số,
  thêm `categories` nền. Override follow-up 1.0 không nâng (không tự thêm PIC vào cách
  chia việc tenant đã đặt).
- **Đồng hồ (QE-14):** như Hồ sơ PO: từ lần chuyển gần nhất vào trạng thái; tạm dừng thì
  không áp dụng, tiếp tục thì tính lại.
- **`follow_ups`:** `product_dev_case_id` (FK tenant+workspace, CASCADE), `ck_follow_ups_one_case`,
  `recipient_user_id`; RLS theo workspace (cả follow-up Hồ sơ PO: danh sách và đóng nay chỉ
  trong workspace của hồ sơ); sweep theo từng workspace (`workspaces_with_cases()`).
- **`pic`** (`supply_chain_follow_ups@1.1.0`): đóng dấu nếu PIC còn membership workspace lúc
  mở, hỏi lại lúc gửi; mỗi loại vẫn phải có scope; leo thang và trễ SLA thêm
  `supply_chain.duty.supply_lead` (QE-18 tạm: TP Cung ứng). PIC đóng dấu đóng được việc.
- **`reassign_pic`:** lệnh riêng (`POST /product-cases/{id}/pic`, `POST /po-cases/{id}/pic`),
  duty `supply_lead` cố định, lý do bắt buộc, người mới là thành viên workspace, audit cùng
  giao dịch; hồ sơ phát triển `ordered`/`cancelled` và Hồ sơ PO `completed`/`cancelled` 409.
  Không có nút trên web (ngoài mục 8 của ticket).
- **Override Elmich:** `scripts/elmich_sla_override.yaml` qua
  `seed_supply_chain_demo.py elmich-sla` (handler của `PUT /sla-policy`; không gọi HTTP để
  khỏi cần API và token), mọi mốc `pending_business_confirmation`, Category tạm Nồi, Chảo,
  Khác (QE-13), nhắc/leo thang 5/10.
- **Tiêu đề thông báo** chỉ nêu số PO hoặc mã đề xuất (QE-20). Báo cáo hằng ngày cho TP
  Cung ứng (QE-19) không thuộc ticket, chưa làm.
- **Web:** chọn Category từ danh sách (lỗi tải danh sách thì báo, không cho gửi trống),
  nhãn Category ở danh sách và chi tiết, ô SLA và huy hiệu trên hồ sơ phát triển, Việc cần
  làm mở đúng loại hồ sơ.

Kiểm: unit 2713+ xanh; integration dw_platform, dw_agent_runtime, dw_supply_chain,
apps/worker; `test_sla_by_category.py` mới (PIC nhận, PIC rời không nhận, W1/W2 không đọc
không đóng, CHECK/FK của bảng, tenant B, nâng override 1.2.0, reassign giữ dấu). Mutation 17
guard đơn vị đều đỏ, cộng RLS workspace của `follow_ups` và `UPGRADE_SLA_OVERRIDES` đỏ ở
integration. Sửa kèm: truy vấn catalog của `test_every_tenant_policy_denies_an_emptied_context`
gọi `has_table_privilege` trên cả `pg_toast` khi planner đẩy điều kiện xuống (đỏ theo thứ tự
chạy sau `test_offboarding.py`); rào bằng subquery `OFFSET 0`. Cần đưa về repo nền.
