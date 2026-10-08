# 02 — BGĐ duyệt mẫu (bước 6) qua approval có người quyết giới hạn

Status: resolved
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

- [x] Người trình không tự duyệt được (tiền tố nghiêm); duyệt thiếu nhận xét bị từ chối.
- [x] `pass_sample` tạo đúng một approval `bod_review`; BGĐ không duyệt thì hồ sơ thành
      `cancelled`, lý do là nhận xét, có một dòng lịch sử. Mutation: graph không áp
      `bod_reject` thì test đỏ.
- [x] `apply_product_action(bod_approve | bod_reject)` gọi thẳng từ API bị từ chối.
- [x] **Test âm người quyết:** người có `approvals.decide` mà thiếu
      `supply_chain.approve.bod` bị từ chối; hồ sơ vẫn `pending_bod_review`.
- [x] **Test âm xuyên tenant, workspace:** approval của hồ sơ ở tenant khác hoặc
      workspace khác trả not found.
- [x] Integration LangGraph: run dừng ở interrupt, worker khởi động lại, quyết định vẫn
      tiếp tục đúng run từ checkpoint.
- [x] Override policy của tenant đổi `required_scope`: approval tạo sau dùng giá trị mới,
      approval đang chờ giữ dấu cũ.
- [x] Mutation: bỏ `required_scope` khỏi payload thì test âm người quyết đỏ (ghi vào
      Comments).
- [x] `make ci` xanh (một lệnh, trên cây cuối của vòng review 1, 2026-10-06).

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

- 2026-10-05 (lát S2, quyết định tạm của lead; chờ Đạt duyệt ở QO-2). Mỗi điểm ghi chỗ
  nó nằm trong code và test giữ nó. Điểm đổi ADR có đoạn sửa đổi trong
  [ADR 0016](../../../../../packages/python/dw_supply_chain/docs/adr/0016-e6-stage-1-is-a-product-development-case-inside-dw-supply-chain.md)
  và [ADR 0020](../../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md)
  (mục "Sửa đổi 2026-10-05").
    1. **QE-08 (tạm):** `bod_reject` → `cancelled`, nhận xét của BGĐ là lý do (thêm vào
       `PRODUCT_REASON_REQUIRED_ACTIONS`); `bod_approve` → `profile_in_progress`, không có
       lý do. `ProductDevelopmentCase.bod_approve/bod_reject`; test
       `test_bgd_approving_moves_the_case_to_the_profile_as_the_decider`,
       `test_bgd_rejecting_cancels_the_case_with_bgds_comment_as_the_reason`.
    2. **Hành động chỉ graph áp:** `GRAPH_ONLY_ACTIONS` ở domain (một chủ).
       `SupplyChainProductActionDuties` đòi duty cho `ProductAction - GRAPH_ONLY_ACTIONS` và
       từ chối khóa của hai hành động này; override lưu trước S2 vẫn hợp lệ
       (`test_an_override_stored_before_step_6_existed_stays_valid`). `AdvanceProductCase`
       từ chối trước khi đọc policy hay hồ sơ; model của route trả 422;
       `available_actions` không bao giờ đưa ra (`test_no_state_offers_a_graph_only_action`).
    3. **Người quyết là actor:** `ApproveAndResumeService.decide` đặt
       `decided_by = context.principal_id` vào payload resume, dựng trong `decide`. Graph
       dùng nó làm actor của bước, dòng lịch sử và audit; DB vẫn trong tenant, workspace
       của run. Resume không có `decided_by` làm run hỏng thay vì ghi "không ai". Test:
       `test_the_resume_names_the_decider_from_their_verified_context`,
       `test_an_interrupt_payload_naming_a_decider_cannot_override_the_real_one`,
       `test_a_decider_named_in_the_start_payload_is_not_the_decider`.
    4. **Xuyên workspace, tenant:** test âm
       `test_bgd_of_another_workspace_or_tenant_finds_no_review` (hai tham số). Workspace
       khác: một thành viên THẬT của workspace thứ hai của Alpha, được cấp `sc_bod` và
       `approver` ở đó. Tenant khác: context của Beta. Cả hai nhận `NotFoundError`;
       `list_pending_by_type_prefix` (hộp `/approvals`) của họ trả `(0, [])` trong khi
       workspace chính của Alpha (đối chứng) liệt kê approval; `pending_by_payload` (trang
       hồ sơ) trả None; hồ sơ vẫn `pending_bod_review`, run vẫn `WAITING_APPROVAL`.
    5. **Khởi động review:** `pass_sample` lưu trước, rồi `EnsureBodReview.ensure`
       (`application/product_reviews.py`). Thread tất định `uuid5(case, 'bod_review',
round)`, run id mới mỗi lần; đụng `uq_worker_runs_active_thread` là "đã có". "Đã
       trình" đọc lại từ hộp approval, không tin `start()`. Phản hồi bước có `review`
       (`raised | already_pending | not_raised | not_waiting`); `not_raised` vẫn 200, bước
       đã lưu. Lane `supply_chain_product_review_reconcile` (đăng ký như các lane khác, có
       tên trong `test_worker.py`, tối đa 20 lần khởi động mỗi nhịp, đi theo (tenant,
       workspace) như follow-up sweep) gọi cùng `ensure`; nó cũng là backfill
       (`test_a_case_waiting_since_before_s2_is_backfilled_by_the_lane`). **Đo dùng lại
       thread** (failure-modes #4): thread có run HỎNG (insert approval bị CHECK từ chối)
       được gọi lại với input mới thì chạy lại từ START, quyết định tiếp tục đúng run mới
       (`test_a_thread_whose_run_failed_is_reused_and_decided_correctly`, saver Postgres
       thật). Thread có run HOÀN TẤT không được đo: domain không đưa được hồ sơ về lại
       `pending_bod_review` cùng vòng sau khi review xong (`profile_in_progress` và
       `cancelled` không quay lại). S3 mà mở đường quay lại thì phải đo trước.
    6. **Trong lúc chờ BGĐ** chỉ có `cancel`; domain từ chối ba bước ngắt ở
       `pending_bod_review`. Resume đọc lại hồ sơ; không còn chờ đúng vòng thì không áp gì,
       ghi audit `supply_chain.product_case.bod_review_superseded`, run hoàn tất với
       outcome `superseded`. Cả hai thứ tự đã test:
       `test_cancelled_before_bgd_decides_the_decision_is_superseded`,
       `test_decided_before_a_cancel_the_cancel_acts_on_the_new_state`. **Mục mở:** approval
       của hồ sơ đã hủy vẫn nằm ở `/approvals` tới khi được quyết.
    7. **Policy** `supply_chain_product_approvals@1.0.0` (`bod_review.required_scope` là `supply_chain.approve.bod`; S4 thêm các bước trình ký thành trường riêng), schema
       `SupplyChainProductApprovals`, `resolve_product_approvals` qua `PolicyOverridePort`
       lúc trình. Không có route PUT. Test
       `test_a_tenant_override_reaches_reviews_raised_after_it_only`. Manifest phát hành sinh
       lại; chỉ giữ `contracts/release/history/manifest-558cb50a3915.json`.
    8. **Trang hồ sơ:** `PendingApprovalsPort` thêm đúng một phương thức,
       `pending_by_payload` (lọc theo workspace người gọi; `SqlPendingApprovalQuery`).
       `GET /product-cases/{id}` trả `pending_review` (`approval_id`, `created_at`, `required_scope`). Trang hiện "Chờ người có quyền BGĐ (supply_chain.approve.bod)
       duyệt", liên kết `/approvals`, không nút quyết; khi chưa trình được thì nói vậy.
    9. **Payload** gồm `product_dev_case_id`, `proposal_code`, `product_name`,
       `sample_round`, `required_scope` (cùng `approval_type`, `reason` mà runner đòi). Kiểu
       `supply_chain.product_action.bod_review`; tiền tố `supply_chain.product_action.` thêm
       vào `strict_approval_prefixes` bằng `|=` ở `wiring.py`.
    10. **Thông báo:** chỉ khi CHÍNH lần `ensure` đó tạo approval, không ở node graph;
        trừ người yêu cầu; `source_key` theo id approval nên chỉ gửi một lần. Protocol hẹp
        `ReviewNotifierPort`, chỉ `deliver`. Quyết định của lead: "người giữ
        `supply_chain.approve.bod`". **Lệch của implementer, chờ lead hoặc Đạt xác nhận:**
        code chỉ gửi cho người giữ CẢ scope đã đóng dấu lẫn `approvals.decide` (người
        không quyết được thì không được mời quyết); test integration khẳng định người chỉ
        có `sc_bod` không nhận. Nếu giữ nguyên văn quyết định: bỏ phép giao với
        `APPROVALS_DECIDE` ở `EnsureBodReview._notify` và sửa test đó.
    11. **Vai:** `sc_bod` = scope của `sc_viewer` (đã có `product_case.read`) +
        `supply_chain.approve.bod`. `approvals.decide` đến từ vai duyệt của nền tảng
        (`approver`, `manager`, `director`) hoặc permission set `approver_boost`; ghi trong
        migration, YAML policy, ADR 0016. `supply_chain.approve.bod` ở phía vận hành của
        `sod_sc_rules_vs_operations`. `test_role_catalogue.py` cập nhật.
    12. **Migration** `5857ae25747a` nới năm CHECK (`state`, `interrupted_state`, và
        `action`, `from_state`, `to_state` của lịch sử); test enum bằng CHECK xanh.
        **Downgrade TỪ CHỐI** (`check_violation`) khi còn hồ sơ ở hoặc dừng từ
        `profile_in_progress`, hay dòng lịch sử có `bod_approve`, `bod_reject` hoặc
        `profile_in_progress`, thay vì viết lại lịch sử. Đã chạy upgrade, downgrade, upgrade
        trên DB local (lúc chưa có dòng nào như vậy).
    13. **Graph** `workflows/advance_product_case_graph.py` (`WORKER_ID` là `supply_chain_advance_product_case`, `GRAPH_VERSION` là `1.0.0`), YAML worker, hằng
        `paths.py`, đăng ký sau khi `product_case_repo` có. Không model, không SQL, không
        adapter cụ thể; node interrupt không đọc ghi gì trước `interrupt()`.
    14. **Integration** với checkpointer Postgres thật:
        `test_a_restarted_worker_resumes_the_same_run_and_bgd_is_the_actor` (một stack mới
        hoàn toàn quyết và resume đúng run). Test âm người quyết:
        `test_the_decide_right_without_bgds_scope_is_refused_and_nothing_moves`.
    15. **Persona:** `scripts/seed_supply_chain_demo.py` tạo Linh (`sc_rnd`) và Khánh
        (`approver` + `sc_bod`) ở workspace chính của Alpha; `keycloak_dev_users.py` đọc
        người dùng từ DB nên không cần danh sách riêng (docstring ghi phải chạy seed trước).
    16. `down_revision = cbf765d02a12`; `alembic heads` cho một head (`5857ae25747a`).

- 2026-10-06 (lát S2, implementer). **Giữ `dw_platform/adapters/persistence/tenant_plans.py`.**
  Người đọc: `ReconcileBodReviews._workspace` (`application/product_reviews.py`) qua
  `TenantPlanPort`, dựng ở `apps/worker/src/dw_worker/consumers/supply_chain.py`. Lane
  không có người nào để lấy gói từ access context, còn runner tính lượt chạy theo `plan_id`
  của run (`RunAllowancePort`). `sweep_context` mang `plan_id="system"`, gói mà
  `PlanEntitlementService` trả 0 lượt, nên không có adapter này thì lane không khởi động
  được run nào. Adapter đọc đúng dòng `entitlements` mà membership lookup đọc, dưới RLS
  của tenant, và trả None cho tenant không active hoặc không có gói (lane bỏ qua, fail
  closed); test `test_the_tenant_plan_is_the_tenants_and_an_unknown_tenant_has_none`.
  Worker dựng runner riêng cho graph duyệt (cùng bảng, checkpointer,
  `PlanEntitlementService(DEFAULT_PLANS)` như API); API resume run khi có quyết định.

- 2026-10-06 **Quyết định còn nợ Đạt:** run chỉ để trình approval (không model, không
  tool) có nên tính vào lượt chạy theo gói không. Hiện tại CÓ tính (cả run từ bước lẫn
  run của lane); hết lượt thì bước vẫn lưu, `review: not_raised`, lane trình lại ở nhịp
  sau.

- 2026-10-06 Hành vi cần biết: nếu run hỏng SAU khi BGĐ đã quyết (ví dụ lỗi DB lúc lưu
  hồ sơ), approval đã được quyết nhưng hồ sơ vẫn `pending_bod_review`, và lane trình một
  review mới trên cùng thread: BGĐ phải quyết lại.

- 2026-10-06 (lát S2, implementer) **Kiểm tra đã chạy** trên `feat/elmich-a-d-s1`, chưa
  commit:
    - `make lint`: xanh (ruff, ruff format, prettier, eslint; cảnh báo eslint chỉ ở file
      không thuộc lát này). Lần đầu đỏ vì prettier ở `product-cases.test.tsx`; đã format.
    - `make typecheck`: mypy "no issues found in 475 source files"; tsc xanh cả 4 gói.
    - `make test-unit` (`pytest -m unit`): 1910 passed, 3 skipped (trước khi thêm test
      `test_a_pending_review_whose_thread_is_free_is_not_raised_twice`; chạy lại sau ở
      dòng cuối).
    - `make test-architecture`: import-linter 9 kept, 0 broken; declared-dependency 12
      gói; `verify_invariants.py` ok.
    - `make test-contract`: 5 passed. `make generate-contracts`: snapshot
      `contracts/openapi/openapi.json` sinh lại giống từng byte bản trong cây (sau khi đổi
      CRLF về LF); `.d.ts` không đổi.
    - `make eval-smoke`: platform_smoke 4/4, supply_chain_smoke 22/22.
    - `make release-manifest-check`: OK `sha256:558cb50a3915…`; history chỉ thêm
      `manifest-558cb50a3915.json`.
    - Integration (`.env` sourced, Postgres thật): `dw_supply_chain` 184 passed;
      `dw_agent_runtime` 61 passed; `dw_platform` 206 passed; `apps/api` + `apps/worker`
      1 passed. Lần chạy gộp đầu tiên hỏng giữa chừng vì máy Docker treo (mọi container
      `unhealthy`), không phải do code; chạy lại từng gói thì xanh như trên.
    - Alembic: upgrade → downgrade -1 → upgrade trên DB local, một head `5857ae25747a`.
    - Vitest `app/supply-chain/__tests__/product-cases.test.tsx`: 19 passed (phủ
      `page.tsx` và `product-case-labels.tsx`).
- 2026-10-06 **Mutation** (script trong scratchpad; mỗi lần chạy control xanh trước, sửa
  một guard, chạy test của nó, khôi phục, kiểm `restored=True`; grep sau đó thấy mọi
  guard đã về):
    - graph không áp `bod_reject` khi không duyệt → ĐỎ (unit graph + integration).
    - bỏ `required_scope` khỏi payload interrupt → ĐỎ
      (`test_the_decide_right_without_bgds_scope_is_refused_and_nothing_moves`).
    - actor là người yêu cầu thay vì `decided_by` → ĐỎ; `decide` lấy `decided_by` từ
      `record.requested_by` → ĐỎ (`test_approval_flow.py`).
    - bỏ kiểm tra "không còn chờ đúng vòng" (superseded) → ĐỎ.
    - `AdvanceProductCase` không từ chối hành động chỉ graph áp → ĐỎ; validator của route
      → ĐỎ (422); policy duty nhận khóa `bod_*` → ĐỎ; `pending_bod_review` đưa ra hơn
      `cancel` → ĐỎ.
    - `pending_by_payload` bỏ lọc workspace → ĐỎ
      (`test_bgd_of_another_workspace_or_tenant_finds_no_review`).
    - bỏ tiền tố nghiêm ở `wiring.py` → ĐỎ; thông báo không trừ người yêu cầu → ĐỎ; lane
      bỏ "tenant không có gói thì không chạy" → ĐỎ.
    - bỏ kiểm tra "đã có review đang chờ" trong `ensure` → lần đầu XANH (seam thread che
      trường hợp thường). Đã thêm
      `test_a_pending_review_whose_thread_is_free_is_not_raised_twice` (review còn chờ
      nhưng run của nó đã kết thúc), chạy lại → ĐỎ.
- 2026-10-06 **reviewing-feature-security:** (1) tenant: hàm SECURITY DEFINER chỉ trả
  id, đã test; `SqlTenantPlans` đặt tenant mỗi giao dịch; hộp approval và trang hồ sơ
  lọc workspace, đã test âm. (2) authorization: `decide` đòi `approvals.decide` cộng scope
  đã đóng dấu, đọc từ dòng; API gọi thẳng `bod_*` bị từ chối ở route và ở handler. Không
  có route chung nào khởi động run theo `worker_id` (`routes/v1/runs.py` chỉ có get,
  timeline, cancel), nên không ai giả được payload có `required_scope` yếu hơn. (3)
  autonomy: graph không tool, không model; chỉ đi tiếp sau quyết định của người có
  scope; test âm ở `ApproveAndResumeService`. (4) nội dung không tin cậy: không áp dụng,
  không model; tên sản phẩm chỉ hiện dạng text. (5) audit: bước của graph lưu hồ sơ và
  audit trong một giao dịch (`repo.save`); run ghi worker, graph version và manifest.
  (6) mặc định: tenant không có gói, resume không có `decided_by`, quyết định không phải
  bool đều từ chối hoặc làm run hỏng; mỗi trường hợp có test.
- 2026-10-06 Chạy lại sau khi thêm test và sửa fixture: `pytest -m unit` 1911 passed, 3
  skipped (hết cảnh báo serializer của pydantic); `make lint` xanh. `make ci` chưa chạy
  thành một lệnh: từng target của nó (lint, typecheck, test-unit, test-architecture,
  test-contract, eval-smoke, release-manifest-check) đều chạy riêng và xanh như trên.

- 2026-10-06 (lát S2, vòng review 1, implementer). Mười lăm phát hiện, kiểm từng cái
  với code; tất cả đúng ở mức nào đó. Đã làm:
    - **Manifest phát hành ở worker (blocker, hai phát hiện trùng).** Image worker không
      có `contracts/release`, nên mọi run lane reconcile khởi động ở uat/production ghi
      `unreleased`. `infra/docker/worker.Dockerfile` thêm
      `COPY contracts/release /app/contracts/release`. Một người đọc `manifest.ref`:
      `dw_agent_runtime.release.release_manifest_ref(repo_root)`, API
      (`paths.release_manifest_ref`) và worker cùng gọi. Worker ở profile triển khai mà
      thiếu file thì từ chối khởi động (`release_manifest_ref_for`), không rơi về
      `unreleased`. Test `apps/worker/tests/unit/test_product_review_reconcile_wiring.py`.
      **Mục mở:** API vẫn rơi về `unreleased` khi thiếu file (image API có file); không
      đổi ở lát này.
    - **Lane công bằng giữa tenant (hai phát hiện trùng).** Lô 20 là một bộ đếm chung,
      tenant đầu bảng hết lượt chạy chiếm cả lô mỗi nhịp. Nay lần khởi động hỏng ĐẦU TIÊN
      của một tenant (bị từ chối như `QuotaExceededError`, hoặc chạy mà không trình được)
      kết thúc lượt của tenant đó trong nhịp; workspace đọc lỗi thì sang workspace kế,
      không kết thúc tenant. Test
      `test_a_tenant_out_of_runs_does_not_starve_the_tenants_after_it`,
      `test_a_tenant_whose_reviews_are_never_raised_costs_one_failed_run_a_tick`.
    - **Thử lại không giới hạn.** Nay tối đa một run hỏng mỗi tenant mỗi nhịp (điểm trên).
      KHÔNG thêm regex dạng scope vào `ApprovalStep`: ADR 0020 sửa đổi 4 (quyết định tạm
      của lead) giữ CHECK của cột là chủ duy nhất của dạng scope, và một bản sao Python là
      failure-modes #2. Không có route PUT cho policy này, nên override sai dạng chỉ đến từ
      ghi thẳng DB. **Mục mở cho Đạt:** override sai dạng vẫn tốn một run hỏng mỗi nhịp
      (288 mỗi ngày với nhịp 5 phút), tính vào lượt chạy của tenant, tới khi ai đó sửa
      dòng; có thể cần cảnh báo hoặc lùi dần.
    - **Requester tự rút review.** Guard có thật (`_enforce_strict_rules` chạy cả khi
      `approve=False`) nhưng không test nào giữ. Thêm
      `test_the_requester_cannot_withdraw_their_own_review_either` (integration: tester
      R&D không có `approvals.decide` lẫn scope BGĐ nhận `ConflictError`; hồ sơ, approval,
      run không đổi) và `test_strict_type_refuses_the_requester_withdrawing_too` (unit
      nền tảng).
    - **Thành viên thật của workspace khác; hộp `/approvals`.** Xem điểm 4 đã sửa ở trên.
    - **Dùng lại thread sau khi `apply` hỏng (failure-modes #4).** Đo bằng saver Postgres:
      `test_a_run_that_fails_after_bgd_decided_is_raised_again_and_applied_once` (lần save
      đầu của graph thua race sau khi BGĐ quyết). Input mới trên cùng thread chạy từ
      START; quyết định cũ không được áp lại; review mới được trình; quyết định mới áp
      đúng một `bod_reject`. Không cần bộ đếm lần thử. Docstring `product_reviews.py` và
      ADR 0016 sửa đổi 4 ghi cả hai dạng hỏng.
    - **Scope quyết của nền tảng bị gõ lại.** `APPROVALS_DECIDE` nay ở
      `dw_platform.domain.approval`; `ApproveAndResumeService.decide` và
      `EnsureBodReview._notify` cùng đọc.
    - **Runner thứ hai ở worker.** Không gộp builder: builder chung phải nằm trong một gói
      cả hai app import, và gói đó phải import adapter cụ thể của `dw_platform` (UoW,
      plan); hợp đồng import hiện tại không có chỗ như vậy. Thay vào đó
      `test_the_review_runner_is_configured_as_the_apis_runner_is` ghim phần phải giống:
      allowance `PlanEntitlementService(DEFAULT_PLANS)`, spend guard, một
      `AutonomyApprovalPolicy`, staleness từ `worker_runs@1.0.0.yaml`, release ref, graph
      và worker đúng version. Không có usage meter: graph duyệt không gọi model.
    - **Test web không thể đỏ.** Bỏ ba khẳng định "không có nút" trong
      `product-cases.test.tsx`; chú thích nói guard ở server (`available_actions`, route
      trả 422).
    - **Trạng thái.** `ready-for-human` (chờ lead và Đạt duyệt; mọi quyết định còn tạm theo
      QO-2), cùng giá trị ở `spec.md` và `supply-chain.md`.
    - **Điểm 10 thông báo** ghi lại là lệch của implementer (điểm 10 ở trên, ADR 0016 sửa
      đổi 7).

    Kiểm tra đã chạy trên cây cuối, chưa commit:
    - `make ci` (một lệnh): exit 0, ">> local CI gate passed". Trong đó ruff, ruff
      format, prettier xanh (eslint chỉ cảnh báo ở file ngoài lát); mypy "no issues found
      in 476 source files", tsc xanh 4 gói; `pytest -m unit` 1919 passed, 3 skipped;
      import-linter 9 kept, 0 broken, declared-dependency 12 gói; test-contract 5 passed;
      eval smoke platform_smoke 4/4, supply_chain_smoke 22/22; release manifest "up to
      date (sha256:558cb50a3915…)", không có file history mới.
    - Integration (`.env` sourced): `dw_supply_chain` + `dw_agent_runtime` 247 passed (gồm 18 của `test_product_bod_review.py`); `dw_platform` 206 passed; `apps/api` + `apps/worker` 1 passed ở lần chạy thứ hai. Lần đầu 1 failed, 1 error: SeaweedFS (cổng 29000) trả 500 liên tiếp khi liệt kê bucket `dw-off-artifacts-*` của test offboarding, không thuộc lát này; chạy lại không đổi code thì xanh.
    - Vitest `app/supply-chain/__tests__/product-cases.test.tsx`: 19 passed.

    Mutation (script trong scratchpad; control xanh trước; khôi phục và kiểm
    `restored=True` sau mỗi lần):
    - lane không kết thúc lượt tenant khi khởi động hỏng → ĐỎ (hai test công bằng).
    - `_enforce_strict_rules` chỉ chạy khi `approve` → ĐỎ (unit nền tảng; integration rút
      review, chạy riêng).
    - `list_pending_by_type_prefix` bỏ lọc workspace → ĐỎ (tham số workspace khác).
    - worker triển khai chấp nhận thiếu manifest → ĐỎ.
    - người nhận thông báo bỏ phép giao với `APPROVALS_DECIDE` → ĐỎ.

- 2026-10-06 (lát S2, vòng review 2, implementer). Hai phát hiện chặn, kiểm cả hai với
  code:
    - **Guard trạng thái tenant của `SqlTenantPlans` không có test (đúng).** Bỏ
      `tables.tenants.c.status == "active"` thì mọi test vẫn xanh. Thêm
      `test_a_tenant_that_is_not_active_has_no_plan_and_the_lane_starts_nothing`
      (integration): hồ sơ Alpha chờ BGĐ mà chưa trình (run bị từ chối), migrator đặt Alpha
      `locked` (dòng `entitlements` vẫn còn, test khẳng định), `plan_of(ALPHA)` là `None`,
      lane chạy mà không trình review; trả Alpha về `active` trong `finally`, chạy lane lần
      nữa thì review được trình, nên trạng thái là lý do duy nhất. Ai đọc `SqlTenantPlans`:
      `ReconcileBodReviews._workspace` (`product_reviews.py`), qua
      `TenantPlanPort.plan_of`, nối ở `apps/worker/src/dw_worker/consumers/supply_chain.py`.
      Mutation: bỏ lọc `status` → ĐỎ (`assert 'professional' is None`); khôi phục, chạy lại
      → xanh. Bỏ `entitlements.tenant_id == tenant_id` vẫn xanh vì RLS (theo
      `app.tenant_id`) thu hẹp thay; đó là mutant tương đương, không phải lỗ hổng.
    - **Điểm 10 thông báo khác nguyên văn quyết định (đúng là lệch; KHÔNG đổi code).**
      Quyết định nói "người giữ `supply_chain.approve.bod`"; code gửi cho người giữ scope đã
      đóng dấu VÀ `approvals.decide`. Giữ nguyên vì luật dự án đứng trên văn bản quyết định
      tạm: hướng dẫn chung §5 ("read path and write path must resolve from the same
      source ... the user meets the disagreement as an error after being invited to act")
      và `code-quality.md` mục DRY (người được giao việc rồi bị từ chối quyền làm). Gửi theo
      nguyên văn sẽ mời người chỉ có `sc_bod` (không có vai `approver`) quyết một review mà
      `ApproveAndResumeService.decide` sẽ từ chối họ. Hai nơi cùng đọc `APPROVALS_DECIDE`
      ở `dw_platform.domain.approval` và `required_scope` đã đóng dấu, nên chỉ một câu trả
      lời cho "ai quyết được". **Vẫn chờ lead hoặc Đạt xác nhận** (QO-2, ADR 0016 sửa đổi
      7); nếu chọn nguyên văn thì bỏ phép giao trong `EnsureBodReview._notify` và sửa
      khẳng định trong `test_passing_a_sample_raises_one_review_and_tells_who_may_decide`.

    Kiểm tra vòng 2 trên cây cuối, chưa commit: `make ci` exit 0 (">> local CI gate
    passed"; mypy 476 file sạch; `pytest -m unit` 1919 passed, 3 skipped; import-linter 9
    kept, 0 broken; contract 5 passed; eval 4/4 và 22/22; manifest
    `sha256:558cb50a3915` không đổi). Integration (`.env` sourced): `dw_supply_chain` 187
    passed (thêm một test), `dw_agent_runtime` 61 passed. `dw_platform` và `apps/worker`
    chưa có kết quả lúc ghi dòng này (lát vòng 2 không sửa code của chúng). Vitest
    `product-cases.test.tsx` 19 passed. `git diff --check` sạch, không CRLF.

- 2026-10-06, lead: verifier vòng 2 còn hai điểm. (1) Mutation sống ở `ReconcileBodReviews._workspace` (`return total, True` -> `False` vẫn xanh): thêm `test_a_stuck_tenant_with_two_waiting_workspaces_still_costs_one_failed_run`; có mutation thì đỏ, khôi phục thì xanh. (2) Người nhận thông báo là người giữ cả `supply_chain.approve.bod` lẫn `approvals.decide`, khác chữ của quyết định 10: lead chấp nhận, vì thông báo không được mời người mà `decide` sẽ từ chối (đường đọc và đường ghi cùng nguồn); vẫn ghi ở QO-8 để Đạt duyệt. Quyết định còn nợ: run chỉ để duyệt (không gọi mô hình) có tính vào hạn mức run của gói không (hôm nay: có).
