# 01 — Duyệt mẫu màu, duyệt thiết kế, mẫu và test trước sản xuất (bước 12–13)

Status: resolved
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

- [x] Policy `true`: vào `production` khi test chưa đạt bị từ chối; sau
      `pass_pre_production_test` thì được. Policy `false`: hành vi hôm nay. Mutation: bỏ
      kiểm điều kiện thì test đỏ (ghi vào Comments).
- [x] Hành động ở trạng thái khác `pre_production` bị từ chối; `pass_pre_production_test`
      thiếu biên bản bị từ chối; biên bản của hồ sơ khác bị từ chối.
- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi
      `packaging_designs` của A; database từ chối dòng tenant B trỏ Hồ sơ PO của A;
      `test_rls_coverage.py` và `test_privileges.py` xanh.
- [x] Override duty lưu trước migration vẫn nạp được.
- [x] `docs/products/elmich/process.md` mục 4 hàng 12–13 cập nhật trạng thái.
- [x] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.1 (sơ đồ con bước 12), 3.2 hàng 12–13, mục 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2 hàng 12, mục 3 "Step 12".

## Comments

- Giả định chờ QE-03: người làm từng bước con như sơ đồ con (Cung ứng duyệt màu và
  thiết kế, R&D test trước SX); không SLA bước con.

**2026-10-07, lead (Đạt ủy quyền quyết điểm mở; safest reading). Lát PK.**

Quyết định tạm:

- **Thứ tự bước con theo sơ đồ con** (process.md 3.1): duyệt màu → duyệt thiết kế →
  nhận mẫu trước SX → test. Yêu cầu sửa màu/thiết kế lặp được tới khi duyệt; duyệt là
  cuối. Test không đạt làm lại được (biên bản mới); đạt là cuối. Domain kiểm, và CHECK
  `ck_packaging_designs_order` giữ thứ tự ở bảng. Ba bước "yêu cầu sửa"/"không đạt" cần
  lý do (CHECK ở lịch sử).
- **Biên bản test** (`pre_production_test_report`) phải của chính Hồ sơ PO này, cùng
  tenant/workspace, tải lên từ khi nhận mẫu trước SX; thiếu, lạ hay cũ là một 409 nêu loại
  thiếu (không lộ là loại nào). FK lịch sử → `case_documents` `ON DELETE NO ACTION`
  (UNIQUE mới `(tenant, workspace, po_case_id, id)`). Duyệt màu, thiết kế không bắt buộc
  chứng từ (ticket không đòi); `colour_sample`, `packaging_design` tải lên ở mục Chứng từ.
- **FK theo workspace** `(tenant_id, workspace_id, po_case_id)`, không phải FK chỉ-tenant
  của ticket: P4 (`62cdcf3bf2d2`) đã chuyển mọi con của PO case sang hình này.
- **Duty:** bảy bước là khóa của `supply_chain_action_duties` (1.2.0, một chủ cho "ai làm
  bước nào trên Hồ sơ PO"): năm bước Cung ứng `ordering`, hai bước test `rnd` (QE-03
  tạm). Migration `85659fd91943` thêm bảy dòng vào override đã lưu (giá trị của tenant
  thắng nếu đã có), `ADD_PACKAGING_STEPS_TO_OVERRIDES`.
- **Luật bước 13:** `supply_chain_packaging@1.0.0` `require_pre_production_test: false`;
  đọc/ghi qua `GET|PUT /packaging-policy` bằng scope `action_duties.read|write` (luật này
  quyết khi nào một bước làm được, như bản đồ duty quyết ai làm); Elmich bật bằng
  `scripts/elmich_packaging_override.yaml` qua `seed_supply_chain_demo.py elmich-packaging`.
  `POCase.start_production(gate)` đòi `ProductionGate` theo chữ ký; cả hai cửa
  (`AdvancePOCase` và nút apply của graph duyệt) hỏi cùng `ProductionGateResolver`; thiếu
  gate thì từ chối. Graph hỏi lúc áp dụng, không lúc yêu cầu.
- **Đua:** lưu bước con khóa dòng PO case `FOR SHARE` và kiểm lại `pre_production` trong
  cùng giao dịch.
- **"Đã báo TP MKT"** là `note` của dòng lịch sử `approve_colour`; PIC nhận thông báo trong
  ứng dụng sau khi lưu (lỗi thông báo không hoàn tác bước). Mỗi bước một audit
  `supply_chain.po_case.<action>` cùng giao dịch.
- **Web:** thẻ "Thiết kế màu và bao bì (bước 12)" trên trang Hồ sơ PO: bốn bước con, luật
  bước 13, lịch sử, nút theo `allowed` do server tính bằng đúng kiểm tra của bước (khóa kèm
  lý do bằng chữ); thứ tự bước do server từ chối, không chép quy tắc sang trình duyệt.
  `DocumentType` TS kiểm `SameType` với enum sinh từ OpenAPI (một chủ: enum Python).

Test: `dw_supply_chain` `test_packaging_design.py` (thứ tự, lý do, biên bản bảy dạng sai,
gate), `test_advance_case_graph.py` (gate ở nút apply), `test_action_duties.py`,
`test_packaging_designs.py` (integration: luồng đủ với luật bật, luật tắt, duty trước khi
đọc, biên bản hồ sơ khác/tenant khác/workspace khác, FK lịch sử, RLS tenant B và workspace
khác đọc 0 dòng và sửa 0 dòng, FK từ chối dòng tenant B trỏ PO case của A, RLS từ chối dòng
mang tenant A, CHECK thứ tự, đua lưu sau khi rời bước 12, override trước migration),
`apps/api` `test_packaging_endpoints.py`, web `packaging-design-card.test.tsx`.

Mutation (đỏ hết): bỏ `gate.check` → test luồng integration và unit đỏ; handler truyền gate
mở → đỏ; graph không hỏi gate → `test_an_approved_start_production_still_asks_the_gate_when_applied`
đỏ; bỏ đọc luật policy → đỏ; bỏ kiểm biên bản cùng hồ sơ → đỏ; bỏ kiểm lại
`pre_production` trong repo → ban đầu XANH (handler đã chặn), thêm test đua rồi đỏ; kiểm
duty đổi thành `po_case.read` → đỏ; bỏ kiểm "ngoài bước 12" ở domain → đỏ.

Phần B (MKT, Thiết kế như người dùng) ngoài phạm vi.
