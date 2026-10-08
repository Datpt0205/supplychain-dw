# 04 — Hồ sơ PO hẹp theo workspace, không chỉ theo tenant

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

Người chỉ thuộc workspace W2 không thấy Hồ sơ PO của W1 cùng tenant, trên cổng cũng như
qua Zalo. Hiện `tenant_isolation_po_cases` (`fddd7579ba27`) chỉ hẹp theo tenant; mục mở
của ADR 0017 (điểm 4) và S5 (bước 5) đã ghi, chưa ai sửa. Z6 đo lại trên Postgres thật
(7/10/2026): câu hỏi Zalo từ W2 về mã PO của W1 trả đúng hồ sơ đó, giống hệt `POST
/case-query` trên web (cùng handler, cùng RLS). Test
`apps/worker/tests/integration/test_zalo_case_query_db.py::test_another_workspace_of_the_same_tenant_is_not_seen`
đang `xfail(strict=True)` vì lý do này.

## Câu hỏi cho Đạt (trước khi làm)

Elmich có muốn phòng mua hàng thấy Hồ sơ PO của mọi workspace trong công ty không? Nếu
có, đó là quyết định sản phẩm và cần ghi (khi đó CLAUDE.md "Every tenant-scoped table
has tenant_id and workspace_id" vẫn đúng, nhưng chính sách đọc là cả tenant, và
`po_case_lines`, `case_documents` đang hẹp theo workspace sẽ lệch với hồ sơ cha). Nếu
không, làm các bước dưới.

## Việc cần làm

1. Migration mới (id ngẫu nhiên của alembic): policy của `po_cases`,
   `po_case_state_transitions`, `supplier_updates`, `delay_impact_analyses` theo hình
   chuẩn `tenant AND (workspace OR current_setting('app.workspace_scope') = 'tenant')`,
   USING và WITH CHECK.
2. Rà mọi đường đọc Hồ sơ PO không cùng workspace với hồ sơ: lane follow-up (đã theo
   workspace), ĐẶT HÀNG (cùng workspace với hồ sơ phát triển), offboarding (đặt
   `app.workspace_scope`), control tower và brief (sẽ hẹp lại; ghi vào Comments).
3. Bỏ `xfail` ở test trên; thêm test RLS cho bốn bảng (W2 không đọc, không ghi dòng của
   W1); `test_rls_coverage.py` phải xanh với hình mới.
4. `InMemoryPOCases` (`dw_supply_chain/testing/po_cases.py`) hẹp thêm theo workspace
   cùng commit, để fake giữ đúng lời hứa của bảng; thêm lại ca eval
   `sc-sec-cross-tenant-chat-po-of-another-workspace` (đã bỏ khỏi `supply_chain@1.5.0` vì
   chưa đúng với cơ sở dữ liệu).

## Tiêu chí chấp nhận

- [x] Test âm: W2 không đọc, không ghi Hồ sơ PO và lịch sử, cập nhật NCC, phân tích trễ
      của W1; Zalo và web cùng trả "không tìm thấy".
- [x] Integration `dw_platform`, `dw_supply_chain`, `apps/worker` xanh.

## Nguồn

- `packages/python/dw_supply_chain/docs/adr/0017-e7-hand-off-via-order-requested.md` điểm 4 ("Mục mở").
- `.claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md` bước 5.
- `.claude/plans/supply-chain/zalo-channel/issues/06-read-only-qa.md` Comments (Z6).

## Comments

### 2026-10-07 — quyết định (lead, Đạt ủy quyền; chọn hướng đóng an toàn)

Câu hỏi ở trên: **KHÔNG**, Hồ sơ PO không đọc được cả tenant. Hẹp theo workspace như hồ
sơ phát triển, dòng PO và chứng từ; phòng ban cần nhiều workspace thì được thêm làm thành
viên của từng workspace. Status: needs-triage → ready-for-agent → resolved trong cùng
ngày. Ghi ở ADR 0017 (sửa đổi P4), nhất quán ở ADR 0016 và 0021.

### 2026-10-07 — làm xong

- **Migration `62cdcf3bf2d2`** (id ngẫu nhiên, down_revision `d56da3dd2146`, một head):
  policy của `po_cases`, `po_case_state_transitions`, `supplier_updates`,
  `delay_impact_analyses` theo hình chuẩn, USING và WITH CHECK; giờ mọi policy
  `tenant_isolation_*` của `supply_chain` đều hẹp theo workspace. FK của ba bảng con tới
  hồ sơ và của phân tích trễ tới cập nhật NCC ghép `workspace_id` (cùng tên);
  `uq_supplier_updates_tenant_id_workspace_id_id` mới; `uq_po_cases_tenant_id_id` và
  `uq_supplier_updates_tenant_id_id` không còn FK nào dùng, bỏ. Index phân trang
  (`ix_po_cases_page`, `_state_page`, `_supplier_page`,
  `ix_po_case_state_transitions_tenant_occurred_at`) có `workspace_id` sau `tenant_id`.
  Dev DB: upgrade → downgrade → upgrade sạch; downgrade trả bốn policy, FK, index về chỉ
  theo tenant và thêm lại hai UNIQUE, không mất dòng.
- **Rà đường đọc** (bước 2): mọi đường có người gọi (web: danh sách, chi tiết, lịch sử,
  cập nhật NCC, phân tích trễ, SLA, Control Tower, daily brief, thanh lệnh `POST
/case-query`; Zalo Z6) đọc bằng `TenantScope.from_access_context`, nên hẹp theo
  workspace của người gọi; Control Tower và brief giờ là của một workspace. Lane
  follow-up đã đi từng workspace qua `workspaces_with_cases()` (S6), không cần hàm
  SECURITY DEFINER mới; bộ lọc workspace trong code của sweep giữ làm lớp thứ hai. Lane
  reconcile chỉ đọc hồ sơ phát triển. ĐẶT HÀNG ghi Hồ sơ PO trong workspace của hồ sơ
  phát triển, cùng giao dịch. Run approval của `advance_case` đọc lại hồ sơ bằng context
  của người yêu cầu (cùng workspace). Offboarding đặt `app.workspace_scope = 'tenant'`.
  Số PO vẫn duy nhất trong tenant (ADR 0017 sửa đổi P4 điểm 3).
- **Test** `dw_supply_chain/tests/integration/test_po_cases_narrowed_by_workspace.py`:
  đối chứng (W1 thấy cả bốn bảng); RLS hẹp bốn bảng với W2; offboarding scope đọc mọi
  workspace nhưng không sang tenant khác; handler web (`GetPOCase`, `ListPOCases`,
  `ListCaseTransitions`, `ListSupplierUpdates`, `ListDelayImpactAnalyses`) trả "không tìm
  thấy"/rỗng; `SubmitSupplierUpdate`, `AnalyzeDelayImpact` không gọi model, không ghi;
  `save` từ W2 là `ConflictError`, trạng thái không đổi; WITH CHECK từ chối dòng ghi vào
  W1 dưới W2; FK ghép từ chối cập nhật NCC, phân tích, lịch sử ở workspace khác hồ sơ.
  Z6: bỏ `xfail(strict=True)` ở `test_another_workspace_of_the_same_tenant_is_not_seen`,
  thêm khẳng định cho đường web (`AnswerCaseQuery` với context đăng nhập W2 → rỗng).
- **Mutation:** bỏ vế workspace khỏi policy → 5 test đỏ (4 file mới, 1 Z6); FK chỉ theo
  tenant → 3 test FK đỏ; fake `InMemoryPOCases` không lọc workspace → eval 50/51.
- **Fake và eval:** `InMemoryPOCases` hẹp theo workspace; `supply_chain@1.6.0` (51 ca) thêm
  lại `sc-sec-cross-tenant-chat-po-of-another-workspace`; release manifest sinh lại.
- **Kiểm:** make lint, typecheck, test-unit, test-architecture, test-contract, eval-smoke,
  release-manifest-check, test-hooks xanh; integration `dw_platform`, `dw_supply_chain`,
  `apps/worker` xanh. Web không đổi.
- **Test cũ phải sửa theo** (ghi ra vì chúng mã hóa hành vi cũ): ba test xóa/đọc bằng SQL
  thẳng chỉ đặt `app.tenant_id` (cascade của cập nhật NCC, phân tích trễ; lý do của lịch
  sử) giờ đặt cả `app.workspace_id`; `test_place_order` khẳng định W2 không còn thấy Hồ
  sơ PO (trước đây thấy hồ sơ, không thấy dòng). Chú thích "po_cases chỉ theo tenant" ở
  `case_documents.py` và test đơn vị của nó sửa lại: phép kiểm workspace là lớp thứ hai.
