# 04 — Hồ sơ PO hẹp theo workspace, không chỉ theo tenant

Status: needs-triage
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

- [ ] Test âm: W2 không đọc, không ghi Hồ sơ PO và lịch sử, cập nhật NCC, phân tích trễ
      của W1; Zalo và web cùng trả "không tìm thấy".
- [ ] Integration `dw_platform`, `dw_supply_chain`, `apps/worker` xanh.

## Nguồn

- `docs/adr/0017-e7-hand-off-via-order-requested.md` điểm 4 ("Mục mở").
- `.claude/plans/supply-chain/stage-1/issues/05-place-order-hand-off.md` bước 5.
- `.claude/plans/supply-chain/zalo-channel/issues/06-read-only-qa.md` Comments (Z6).

## Comments
