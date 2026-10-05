# 01 — Duyệt mẫu màu, duyệt thiết kế, mẫu và test trước sản xuất (bước 12–13)

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/case-documents/issues/01-case-documents.md
Area: supply-chain

## Mục tiêu

Các bước con của bước 12 mà Cung ứng và R&D làm được ghi trên Hồ sơ PO; Hồ sơ PO chỉ
vào `production` khi R&D đã đạt test trước sản xuất, nếu tenant bật luật đó (spec,
Mục tiêu).

## Việc cần làm

1. **Migration** (id hex ngẫu nhiên): `supply_chain.packaging_designs`, một dòng mỗi
   Hồ sơ PO (UNIQUE `(tenant_id, po_case_id)`), FK `(tenant_id, po_case_id)` tới
   `po_cases (tenant_id, id)` `ON DELETE CASCADE` như `bc3f0c1279fd`; cột
   `colour_status`, `design_status` (CHECK `pending | revision_requested | approved`),
   `pre_production_sample_received_at`, `pre_production_test` (CHECK
   `pending | passed | failed`), `version`; RLS ENABLE + FORCE hình workspace chuẩn;
   grant trong migration; lịch sử theo mẫu `po_case_state_transitions`.
2. **Hành động** (đọc duty từ policy duty, phiên bản mới và migration dữ liệu cho
   override đã lưu, như S5 bước 3): `approve_colour`, `request_colour_revision`\*,
   `approve_design`, `request_design_revision`\*, `receive_pre_production_sample`
   (`ordering`); `pass_pre_production_test`, `fail_pre_production_test`\* (`rnd`, từ
   S1). Chỉ khi Hồ sơ PO ở `pre_production`. Mỗi hành động ghi audit. `approve_colour`
   ghi "đã báo TP MKT" vào lịch sử và thông báo cho PIC.
3. **Chứng từ** qua lát D: `colour_sample`, `packaging_design`,
   `pre_production_test_report` thêm vào danh sách `doc_type`; `pass_pre_production_test`
   và `fail_pre_production_test` cần một `pre_production_test_report` của hồ sơ này.
4. **Điều kiện vào sản xuất:** policy `supply_chain_packaging@1.0.0`
   (`require_pre_production_test`, mặc định `false` để Hồ sơ PO có sẵn và tenant khác
   không đổi hành vi; override của Elmich đặt `true`) qua `PolicyOverridePort`. Khi
   `true`, `start_production` (`pre_production` → `production`) bị từ chối có tên trừ
   khi `pre_production_test = passed`.
5. **Web:** khối "Thiết kế màu và bao bì" trên trang chi tiết Hồ sơ PO (bốn bước con,
   trạng thái, chứng từ, nút theo duty, khóa kèm lý do khi thiếu duty).

## Tiêu chí chấp nhận

- [ ] Policy `true`: vào `production` khi test chưa đạt bị từ chối; sau
      `pass_pre_production_test` thì được. Policy `false`: hành vi hôm nay. Mutation: bỏ
      kiểm điều kiện thì test đỏ (ghi vào Comments).
- [ ] Hành động ở trạng thái khác `pre_production` bị từ chối; `pass_pre_production_test`
      thiếu biên bản bị từ chối; biên bản của hồ sơ khác bị từ chối.
- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi
      `packaging_designs` của A; database từ chối dòng tenant B trỏ Hồ sơ PO của A;
      `test_rls_coverage.py` và `test_privileges.py` xanh.
- [ ] Override duty lưu trước migration vẫn nạp được.
- [ ] `docs/products/elmich/process.md` mục 4 hàng 12–13 cập nhật trạng thái.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.1 (sơ đồ con bước 12), 3.2 hàng 12–13, mục 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2 hàng 12, mục 3 "Step 12".

## Comments

- Giả định chờ QE-03: người làm từng bước con như sơ đồ con (Cung ứng duyệt màu và
  thiết kế, R&D test trước SX); không SLA bước con.
