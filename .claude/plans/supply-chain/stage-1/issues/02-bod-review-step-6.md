# 02 — BGĐ duyệt mẫu (bước 6) qua approval có người quyết giới hạn

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/stage-1/issues/01-product-case-steps-1-5.md, .claude/plans/supply-chain/approval-decider-scope/issues/01-required-scope.md
Area: supply-chain

## Mục tiêu

Mẫu đạt được trình BGĐ; chỉ người giữ scope BGĐ quyết; duyệt thì hồ sơ sang làm Profile,
không duyệt thì hủy có lý do.

## Việc cần làm

1. **Graph** `workflows/advance_product_case_graph.py` theo hình
   `advance_case_graph.py` (interrupt, quyết, áp qua `apply_product_action`):
   `WORKER_ID = "supply_chain_advance_product_case"`, `GRAPH_VERSION = "1.0.0"`,
   `APPROVAL_TYPE_PREFIX = "supply_chain.product_action."`;
   `configs/workers/supply_chain_advance_product_case.yaml`; đăng ký graph và worker ở
   composition root.
2. **Approval** `supply_chain.product_action.bod_review`, payload có `required_scope`
   đọc từ policy mới `configs/policies/supply_chain_product_approvals@1.0.0.yaml`
   (`bod_review.required_scope: supply_chain.approve.bod`), qua `PolicyOverridePort`.
3. **Tiền tố nghiêm:** thêm `"supply_chain.product_action."` vào
   `wiring.approval_flow.strict_approval_prefixes` bằng `|=` ở seam của context.
4. **Trạng thái mới** `profile_in_progress`; hành động `bod_approve` →
   `profile_in_progress`, `bod_reject`* → `cancelled` với nhận xét của BGĐ là lý do
   (ADR 0016). Chỉ graph áp hai hành động này. Khác `advance_case_graph`, nơi không duyệt
   thì không áp gì (`test_rejecting_applies_nothing`): graph này áp `bod_reject`.
   **`pass_sample` khởi động run:** handler áp `pass_sample` (→ `pending_bod_review`) rồi
   tạo run trong cùng lệnh; không có nút trình riêng.
5. **Vai** `sc_bod` (scope `supply_chain.approve.bod`) bằng migration `platform.roles`.
   Người BGĐ giữ thêm vai duyệt của nền tảng (`approvals.decide`).
6. **Thông báo:** khi approval được tạo, `deliver` một thông báo cho người giữ
   `supply_chain.approve.bod` trong workspace (qua `ScopeHoldersPort` sẵn có), liên kết
   về `/approvals`.
7. **Web:** trang chi tiết hồ sơ hiện approval đang chờ và người được quyết khi ở
   `pending_bod_review`; không có nút `bod_approve`/`bod_reject` trên trang hồ sơ (quyết
   ở `/approvals`).

## Tiêu chí chấp nhận

- [ ] Người trình không tự duyệt được (tiền tố nghiêm); duyệt thiếu nhận xét bị từ chối.
- [ ] `pass_sample` tạo đúng một approval `bod_review`; BGĐ không duyệt thì hồ sơ thành
      `cancelled`, lý do là nhận xét, có một dòng lịch sử. Mutation: graph không áp
      `bod_reject` thì test đỏ.
- [ ] `apply_product_action(bod_approve | bod_reject)` gọi thẳng từ API bị từ chối.
- [ ] **Test âm người quyết:** người có `approvals.decide` mà thiếu
      `supply_chain.approve.bod` bị từ chối; hồ sơ vẫn `pending_bod_review`.
- [ ] **Test âm xuyên tenant, workspace:** approval của hồ sơ ở tenant khác hoặc
      workspace khác trả not found.
- [ ] Integration LangGraph: run dừng ở interrupt, worker khởi động lại, quyết định vẫn
      tiếp tục đúng run từ checkpoint.
- [ ] Override policy của tenant đổi `required_scope`: approval tạo sau dùng giá trị mới,
      approval đang chờ giữ dấu cũ.
- [ ] Mutation: bỏ `required_scope` khỏi payload thì test âm người quyết đỏ (ghi vào
      Comments).
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 6; mục 5 điểm 5.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2 hàng 6 ("approval mechanism can be reused ... wired to
  `CaseAction` only"), mục 3 hàng `PENDING_BOD_REVIEW`.
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, "Workflows" (`advance_case_graph.py`, `interrupt()`, tiền tố).

## Comments

- Giả định: BGĐ không duyệt là hủy (QE-08).

- 2026-10-05 (lát S1, lead): S2 đổi `pass_sample` để khởi động run approval bước 6
  (`supply_chain.product_action.bod_review`, `required_scope` của lát A) trong CÙNG lệnh
  với chuyển trạng thái. Hồ sơ đã ở `pending_bod_review` từ trước S2 (S1 không khởi động
  run nào) cần backfill hoặc một cách khởi động tường minh; nếu không chúng kẹt ở đó.
  Hôm nay `pending_bod_review` chỉ có bước ngoại lệ và hủy (`available_actions` của
  aggregate).
