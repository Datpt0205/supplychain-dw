# 04 — Mã hàng chính thức, SKU, trình ký BGĐ và Kế toán (bước 9)

Status: resolved
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

- [x] Hai giao dịch song song tạo cùng mã: một thành công, một 409 (test integration).
- [x] `add_sku` trước `issue_item_code` bị từ chối ở aggregate; chèn thẳng SKU không mã
      hàng bị database từ chối.
- [x] Cùng mã hàng, cùng mã SKU ở tenant khác: được (độc lập theo tenant).
- [x] **Test âm RLS** cho `item_codes`, `skus`; **test âm route** xuyên tenant, workspace.
- [x] Trình ký: người chỉ có `supply_chain.approve.accounting` không quyết được bước BGĐ;
      thứ tự theo policy; override của tenant đổi thứ tự thì graph theo thứ tự mới cho
      hồ sơ trình sau.
- [x] Không duyệt ở bước Kế toán: hồ sơ về `item_coding`, mã hàng và SKU giữ nguyên.
- [x] Mutation: bỏ ánh xạ tên ràng buộc thì lỗi trùng ra 500 và test đỏ (ghi vào
      Comments).
- [ ] `make ci` xanh. (Chạy từng cổng thành phần, xem Comments; build container và
      compose smoke không chạy ở lát này.)

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 9; mục 5 điểm 2, 3.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3 (`ItemCode`, `Sku`, hàng `ITEM_CODING`, `PENDING_SIGNOFF`).
- `.claude/rules/code-quality.md`, "Single responsibility" (mẫu đổi lỗi ràng buộc của
  tách nhiệm).

## Comments

- Giả định: ký tuần tự BGĐ rồi Kế toán, cả hai bắt buộc, không ngưỡng giá trị (QE-10);
  thứ tự là policy nên đổi không cần code.
- "Chốt số lượng SKU" chưa rõ nghĩa (QE-11); `planned_quantity` để NULL được.

- 2026-10-07 (lát S4, quyết định tạm của implementer; Đạt ủy quyền quyết các điểm mở,
  "an toàn nhất"). Đoạn sửa đổi ở
  [ADR 0016](../../../../../docs/adr/0016-e6-stage-1-is-a-product-development-case-inside-dw-supply-chain.md),
  [ADR 0018](../../../../../docs/adr/0018-e8-item-code-and-sku-uniqueness-owned-by-the-database.md)
  và [ADR 0020](../../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md)
  (mục "Sửa đổi 2026-10-07, lát S4"). Mỗi điểm ghi chỗ nó nằm trong code và test giữ nó.
    1. **QE-10 (tạm): tuần tự BGĐ rồi Kế toán, cả hai bắt buộc.** Policy
       `supply_chain_product_approvals@1.1.0` thêm `signoff`, danh sách có thứ tự
       (`step`, `label`, `required_scope`; không rỗng, không trùng `step`). Danh sách đọc
       LÚC TRÌNH và đi theo run, nên hồ sơ đang chờ giữ thứ tự cũ; override 1.0.0 (trước khi
       có `signoff`) lấy `signoff` của nền tảng (`SupplyChainProductApprovals.from_stored`).
       Mỗi bước là một approval `supply_chain.product_action.signoff` đóng dấu
       `required_scope` của bước, payload có `step`, `step_label`, `step_no`, `steps`. Một
       bước không duyệt → graph áp `signoff_reject` (→ `item_coding`, nhận xét là lý do),
       bước sau không được trình. Test unit `test_product_signoff_graph.py` (11), unit
       `test_product_approvals.py`, integration
       `test_bgd_then_accounting_sign_one_run_and_the_case_is_ready_to_order`,
       `test_accounting_not_approving_returns_the_case_to_coding_with_its_codes`,
       `test_bgd_not_approving_raises_no_accounting_step`,
       `test_a_tenant_order_reaches_submissions_after_it_only`.
    2. **Graph của bước 9 là worker riêng** (`supply_chain_product_signoff`, graph 1.0.0,
       `workflows/product_signoff_graph.py`), cùng mẫu với graph của ticket 02 (node
       interrupt không đọc ghi gì trước `interrupt()`, người quyết là actor lấy từ
       `decided_by` của resume, đọc lại hồ sơ sau mỗi quyết định). **Lệch chữ ticket**
       ("graph của ticket 02 chạy các bước"): sửa graph 1.0.0 đang có run BGĐ chờ sẽ đổi một
       graph có phiên bản mà không nâng phiên bản; worker riêng giữ các run đó resume được.
       Các bước nối nhau trong MỘT run (đã đo: resume tạo approval thứ hai trên cùng run, run
       về `WAITING_APPROVAL` lần nữa). Sau mỗi bước duyệt (trừ bước cuối) ghi audit
       `signoff_step_approved` với người quyết là actor.
    3. **Một hàm ensure, lane mở rộng chứ không chép:** `EnsureBodReview` đổi thành
       `EnsureProductApproval` (bảng `_WAITS` theo trạng thái chờ: loại approval, worker,
       vòng đặt tên thread, input, lời báo), `ReconcileBodReviews` thành
       `ReconcileProductApprovals`, `BodReviewPort` thành `ProductApprovalPort`. Thread
       `uuid5(case, 'signoff', signoff_round)`; cột mới `product_dev_cases.signoff_round` (số
       lần trình). Thread của BGĐ giữ nguyên công thức. Lane giữ tên
       `supply_chain_product_review_reconcile`; hàm SECURITY DEFINER
       `workspaces_awaiting_bod_review()` thay bằng `workspaces_awaiting_product_approval()`
       (cả `pending_bod_review` và `pending_signoff`, chỉ trả id, partial index mới). Test
       `test_the_lane_raises_a_missing_signoff_as_the_submitter`,
       `test_a_refused_start_keeps_the_submission_and_the_lane_raises_the_signoff`.
    4. **Thông báo người quyết thật, cả bước sau:** người trong workspace giữ CẢ dấu của
       approval đó lẫn `approvals.decide`, trừ người trình; `source_key`
       `supply_chain.signoff:<approval_id>` nên mỗi người một lần. Bước Kế toán được tạo
       TRONG run lúc BGĐ quyết (ở API), nơi không có ai để báo; lane báo mọi approval đang chờ
       ở mỗi nhịp (gửi lại cùng khóa là no-op), nên Kế toán được báo chậm tối đa một chu kỳ
       lane (mặc định 300 giây). Approval vẫn hiện ở `/approvals` ngay. Lane nay cũng báo lại
       BGĐ review đang chờ cho người mới nhận scope (cùng cơ chế). Test
       `test_the_lane_tells_the_signers_of_a_later_step_raised_inside_the_run` (unit),
       integration bước BGĐ/Kế toán ở điểm 1.
    5. **Hành động chỉ graph áp:** `GRAPH_ONLY_ACTIONS` thêm `signoff_approve`,
       `signoff_reject`; policy duty từ chối khóa của chúng; `AdvanceProductCase` từ chối
       trước khi đọc gì; route 422. Test
       `test_the_signoffs_outcome_is_never_a_step_even_for_the_ordering_duty`,
       `test_the_signoffs_outcomes_from_the_api_are_refused`.
    6. **Ai ký, test âm:** người chỉ có `supply_chain.approve.accounting` không THẤY bước BGĐ
       (404, theo sửa đổi ADR 0020 2026-10-07), cũng vậy người chỉ có `approvals.decide` và
       `platform_admin` không dấu; BGĐ không thấy bước Kế toán; người trình không tự duyệt và
       không tự rút (tiền tố nghiêm), nhận xét bắt buộc; người của workspace khác, tenant khác
       không thấy gì. Test `test_a_decider_without_the_stamped_scope_does_not_find_the_step`
       (3 tham số), `test_bgd_cannot_sign_the_accounting_step`,
       `test_the_submitter_cannot_sign_their_own_submission`,
       `test_a_signer_of_another_workspace_or_tenant_finds_no_step` (2).
    7. **Vai:** `sc_finance` thêm `supply_chain.approve.accounting` (không thêm
       `sc_accounting`, QE-16 còn mở); scope này ở phía vận hành của
       `sod_sc_rules_vs_operations` như `supply_chain.approve.bod`. Test
       `test_kế_toán_signs_through_sc_finance_and_nobody_else`,
       `test_the_role_catalogue_gives_kế_toán_the_accounting_stamp`.
    8. **QE-11 (tạm), lệch đề xuất của lead:** KHÔNG thêm pattern mã vào policy. ADR 0018
       (Accepted) ghi "không kiểm định dạng cho tới khi Elmich gửi quy tắc"; một regex do
       tenant đặt là mặt ReDoS phải chặn riêng, và một policy chưa ai ghi đè là
       failure-modes #1. Mã cắt khoảng trắng ở domain, CHECK `ck_item_codes_code` (cắt
       khoảng trắng, khác rỗng, tối đa 100 ký tự) ở database (SKU cũng vậy, `variant_label` bắt buộc,
       `planned_quantity` NULL hoặc > 0). Không trùng theo TENANT, phân biệt hoa thường
       (UNIQUE thường; "mh-1" và "MH-1" là hai mã) — chờ QE-11. SKU là dòng `skus` dưới mã
       hàng của CHÍNH hồ sơ (FK ghép). Không đồng bộ ERP.
    9. **Ràng buộc trùng do database, 409 theo tên:** `uq_item_codes_tenant_id_code`,
       `uq_skus_tenant_id_sku_code` (và một mã mỗi hồ sơ) → `ConflictError` nêu mã trùng
       (`details.item_code` / `sku_code`, `constraint`). Hai giao dịch song song cùng mã: một
       lưu, một 409, bước, lịch sử, audit của người thua rollback. Test
       `test_two_transactions_issuing_one_item_code_at_once_one_is_a_409`,
       `test_two_transactions_adding_one_sku_code_at_once_one_is_a_409`,
       `test_another_tenant_may_use_the_same_item_and_sku_codes`,
       `test_an_item_code_taken_in_the_tenant_is_409_naming_it` (API).
    10. **FK không phải RESTRICT như ticket viết:** mã hàng và SKU → hồ sơ
        `ON DELETE CASCADE` như mọi bảng con của S1; SKU → mã hàng FK ghép `NO ACTION`. Lý do: purge
        offboarding chỉ xóa bảng `dw_app` được DELETE, theo thứ tự tên, và dựa vào cascade
        của hồ sơ; `item_codes` không có DELETE nên cascade là đường ra duy nhất, RESTRICT sẽ
        chặn chính cascade đó. NO ACTION kiểm cuối câu lệnh; mọi lệnh xóa mã hàng còn SKU
        khác vẫn bị từ chối như RESTRICT. Test
        `test_an_item_code_with_skus_cannot_be_deleted`, offboarding mở rộng tới mã hàng có
        2 SKU (`test_offboarding_purges_product_cases_rounds_and_documents_of_one_tenant`).
    11. **Quyền:** `item_codes` SELECT, INSERT, UPDATE (`code`) — không DELETE; `skus`
        SELECT, INSERT, DELETE — không UPDATE. Test catalog ở `test_privileges.py` và
        `test_the_application_holds_exactly_the_grants_each_table_needs`. RLS FORCE, dạng
        workspace của `CLAUDE.md`; test âm đọc, ghi dòng mang tenant/workspace của chủ (WITH
        CHECK), DELETE/UPDATE thô theo id (0 dòng):
        `test_another_tenant_or_workspace_reads_no_item_code_or_sku`,
        `test_another_tenant_or_workspace_cannot_write_or_remove_the_owners_codes`;
        `test_a_sku_written_directly_needs_an_item_code_of_its_own_case` (NOT NULL, FK ghép,
        mã hàng của hồ sơ khác).
    12. **Bước mã hóa:** `issue_item_code`, `add_sku`, `remove_sku` là bước có dòng lịch sử
        (`item_coding` → `item_coding`), audit (nêu mã), tăng `version`; không dời
        `stage_entered_at` (truy vấn bỏ dòng tự vòng). `issue_item_code` khi đã có mã là SỬA
        cùng dòng (SKU giữ nguyên), mã giống hệt là 422. `add_sku` trước mã hàng: 409 ở
        aggregate. Thiếu gì để trình/thêm SKU do một hàm domain `unmet_for` trả, page khóa
        nút theo `unmet` của server. 409 `details.missing` là chuỗi (`"item_code,sku"`): lớp
        lỗi của API ép mọi giá trị `details` thành chuỗi.
    13. **Trong lúc chờ ký** chỉ hủy (không tạm dừng, không sửa mã, SKU). Hủy hoặc quyết định
        khi hồ sơ đã đổi: graph đọc lại hồ sơ sau MỖI quyết định; không còn chờ đúng vòng ký
        thì không áp gì, không trình bước sau, audit `signoff_superseded`, outcome
        `superseded`. Test `test_cancelled_while_a_step_waits_the_decision_is_superseded` (ở
        bước 1 và bước 2), unit `test_a_decision_for_an_earlier_signoff_round_is_superseded`.
        **Mục mở:** approval của hồ sơ đã hủy vẫn nằm ở `/approvals` tới khi được quyết (như
        S2). Hồ sơ hủy giữ mã hàng và SKU: mã đã cấp không tái dùng trong tenant (an toàn
        cho ERP); Elmich muốn tái dùng thì là quyết định riêng.
    14. **`ready_to_order`:** chỉ ngoại lệ và hủy; ĐẶT HÀNG là ticket 05.
    15. **Z5:** quyết định trên Zalo sau khi xem trên portal dùng cùng cổng phiên bản hồ sơ
        (tiền tố `supply_chain.product_action.`), payload giữ khóa `product_dev_case_id`.
        Test `test_accounting_signs_on_zalo_after_a_view_and_the_signoff_applies`.
    16. **Policy duty 1.2.0:** bốn bước của bước 9 thuộc `ordering`; override lưu ở 1.0.0 hoặc
        1.1.0 lấy duty nền tảng cho các bước thêm sau phiên bản của nó
        (`STEPS_ADDED_AFTER`, thay `STEPS_ADDED_AFTER_1_0_0`). Test
        `test_a_1_1_0_override_takes_the_platforms_duty_for_step_nine_only`,
        `test_step_nine_needs_the_ordering_duty` (R&D, TP Cung ứng, chỉ đọc: 403 trước khi đọc
        hồ sơ, 12 tham số).
    17. **Web (antd, E-HSDT v3):** thẻ "Mã hàng và SKU (bước 9)" trên trang hồ sơ
        (`components/supply-chain/item-coding-card.tsx`): cấp/sửa mã, bảng SKU (mã không cắt
        ngắn), thêm SKU, bỏ SKU qua `modal.confirm` (nút nguy hiểm, focus mặc định "Giữ lại");
        mã trùng hiện NGAY TẠI TRƯỜNG từ 409 của server; mỗi điều khiển khóa có lý do bằng
        chữ (bước khác, tạm dừng, chờ ký, đã ký, thiếu duty, thiếu mã hàng). Khối "Hồ sơ chờ
        ký" với `Steps` (Đã ký / Đang chờ ký / Chưa tới) theo thứ tự đã đóng dấu, dấu của bước
        đang chờ, liên kết `/approvals/<id>`; không thấy approval thì nói thật cả hai khả năng
        (chưa tạo được, hoặc người xem không phải người ký/người trình). Vitest 7 ca mới.
    18. **Migration** `76bd1b5fc546` (id ngẫu nhiên của alembic, down_revision
        `dbb8c3359981`, một head). Downgrade TỪ CHỐI khi còn dòng của bước 9. Đã chạy upgrade →
        downgrade -1 → upgrade trên DB local.
- 2026-10-07 (lát S4) **Mutation** (`mutate.py` tạm, mỗi lần sửa một guard, chạy test của
  nó, khôi phục; mutation không áp được thì báo, không tính là bắt được): 16/16 đỏ.
  M1 bỏ ánh xạ `uq_item_codes_tenant_id_code` → lỗi trùng thành `IntegrityError` (API trả
  500), `test_two_transactions_issuing_one_item_code_at_once_one_is_a_409` đỏ; M2 cùng cho
  SKU; M3 `add_sku` trước mã hàng; M4 trình khi thiếu mã/SKU; M5 bỏ `signoff_*` khỏi
  `GRAPH_ONLY_ACTIONS` (đỏ ngay lúc nạp fixture policy duty, vì policy đòi duty cho chúng);
  M6 sửa mã khi đang chờ ký; M7 tạm dừng khi chờ ký; M8 graph trình bước sau không đọc lại
  hồ sơ; M9 bỏ so `signoff_round`; M10 actor lấy từ run thay vì `decided_by`; M11 lane không
  báo bước sau; M12 đóng dấu theo policy nền tảng thay vì của tenant; M13 bước mã hóa bỏ kiểm
  duty; M14 bỏ policy RLS của `skus`; M15 FK SKU không trỏ mã hàng; M16 trang hồ sơ không
  đọc approval ký.
- 2026-10-07 (lát S4) **Kiểm tra đã chạy** trên `feat/elmich-a-d-s1`: `make lint` xanh (chỉ
  cảnh báo eslint cũ ở file ngoài lát); `make typecheck` xanh (mypy 518 file, tsc 4 gói);
  `make test-unit` 2518 passed, 3 skipped; `make test-architecture` (import-linter 9 kept,
  declared-dependency 12 gói, `verify_invariants.py` ok); `make test-contract` 5 passed;
  `make eval-smoke` platform 4/4, supply_chain 30/30; `make release-manifest-check` OK
  `sha256:5a7a9bf68765…` (history thêm `manifest-5a7a9bf68765.json`); `make test-hooks`
  76 passed; integration (Postgres thật của repo này): `dw_supply_chain` 254, `dw_platform`
  257, `dw_agent_runtime` 92, `apps/worker` 44, tất cả passed; vitest toàn bộ `@dw/web` 315
  passed (32 file); `make generate-contracts` sinh lại `openapi.json` và
  `supply-chain.d.ts` (đổi về LF). Security review (`reviewing-feature-security`): cách ly
  tenant, phân quyền, audit cùng giao dịch có test âm ở trên; không có tool agent, không
  nội dung không tin cậy, không Dockerfile hay lockfile đổi (configs được COPY cả thư mục).
