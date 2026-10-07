---
status: Accepted
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # bước 1-9
    - ../products/elmich/process.md#4-đối-chiếu-với-code-và-ticket
    - ../../packages/python/dw_supply_chain/src/dw_supply_chain/domain/po_case.py # CaseState, apply_action
    - ../../configs/policies/supply_chain_action_duties@1.0.0.yaml
---

# E6. Giai đoạn 1 (bước 1–9) là Hồ sơ phát triển sản phẩm, một aggregate riêng trong `dw_supply_chain`

Bước 1–9 của quy trình Elmich chưa có code. Quyết định: chúng là aggregate
`ProductDevelopmentCase` (Hồ sơ phát triển sản phẩm) trong cùng context
`dw_supply_chain`, cạnh `POCase`, theo đúng các mẫu `POCase` đã dùng: dataclass có
method canh điều kiện, enum hành động đóng, một bảng dispatch
`apply_product_action`, mỗi chuyển trạng thái ghi một dòng lịch sử, duty của từng
hành động là policy tenant ghi đè được, approval qua graph HITL có tiền tố nghiêm.

## Trạng thái và hành động

| Từ                      | Hành động (bước)                                                                      | Tới                                 | Ai                                                                             |
| ----------------------- | ------------------------------------------------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------ |
| (mới)                   | `propose` (1)                                                                         | `proposed`                          | duty `ordering`; PIC đóng dấu từ người tạo                                     |
| `proposed`              | `request_sample` (2)                                                                  | `sample_requested`                  | `ordering`                                                                     |
| `sample_requested`      | `receive_sample` (2)                                                                  | `sample_testing`                    | `rnd`                                                                          |
| `sample_testing`        | `pass_sample` (3, Đạt)                                                                | `pending_bod_review`                | `rnd`, kèm Biên bản đánh giá mẫu; khởi động run approval bước 6                |
| `sample_testing`        | `request_revision`\* (3, 4)                                                           | `revision_requested`                | `rnd`, kèm Phiếu yêu cầu chỉnh sửa                                             |
| `revision_requested`    | `receive_revised_sample` (5)                                                          | `sample_testing`, vòng mẫu + 1      | `rnd`                                                                          |
| `sample_testing`        | `reject_sample`\* (3, Hủy)                                                            | `cancelled`                         | `rnd`                                                                          |
| `pending_bod_review`    | `bod_approve` / `bod_reject`\* (6)                                                    | `profile_in_progress` / `cancelled` | graph áp sau approval `supply_chain.product_action.bod_review`                 |
| `profile_in_progress`   | `complete_profile` (7)                                                                | `supplier_confirmation`             | `rnd`, kèm BM04                                                                |
| `supplier_confirmation` | `confirm_with_supplier` (8)                                                           | `item_coding`                       | `supply_lead`, kèm email xác nhận của NCC                                      |
| `item_coding`           | `issue_item_code`, `add_sku`, `remove_sku` (9)                                        | `item_coding`                       | `ordering`                                                                     |
| `item_coding`           | `submit_for_signoff` (9)                                                              | `pending_signoff`                   | `ordering`; cần mã hàng chính thức và ít nhất một SKU; khởi động run trình ký  |
| `pending_signoff`       | `signoff_approve` (9, bước cuối)                                                      | `ready_to_order`                    | graph áp sau mọi bước approval `supply_chain.product_action.signoff`           |
| `pending_signoff`       | `signoff_reject`\* (9, bất kỳ bước nào)                                               | `item_coding`                       | graph áp; nhận xét của người không duyệt là lý do; mã hàng, SKU giữ nguyên     |
| `ready_to_order`        | `place_order` (ĐẶT HÀNG)                                                              | `ordered` (kết thúc)                | `ordering`; tạo Hồ sơ PO ([ADR 0017](0017-e7-hand-off-via-order-requested.md)) |
| trạng thái đang chạy    | `wait_for_external`\*, `flag_blocked`\*, `flag_manual_review`\*, `resume`, `cancel`\* | như `POCase`                        | `exceptions`, `ordering`; ở `pending_bod_review` chỉ `cancel` (sửa đổi S2)     |

\* bắt buộc lý do. Nhãn tiếng Việt của từng trạng thái ở glossary.

**Approval áp cả hai kết cục.** `bod_approve`, `bod_reject`, `signoff_approve`,
`signoff_reject` không phải nút người dùng bấm: chỉ graph áp chúng, sau quyết định
approval. Khác `advance_case_graph` của Hồ sơ PO, nơi không duyệt thì không áp gì
(`test_advance_case_approval.py::test_rejecting_applies_nothing`): ở đây không duyệt
cũng là một chuyển trạng thái, với nhận xét của người quyết làm lý do. Run do
`pass_sample` (bước 6) và `submit_for_signoff` (bước 9) khởi động trong cùng lệnh;
không có nút "trình" riêng.

**PIC** là `pic_user_id`, NOT NULL, đóng dấu từ `context.principal_id` lúc `propose`,
không bao giờ suy lại (quy tắc PIC của Elmich, process.md mục 3). Đổi PIC là hành
động `reassign_pic` có lý do và audit.

**Duty và vai:** bước của Cung ứng dùng duty `ordering` sẵn có (cùng người làm
bước 10–16); thêm duty `rnd` (vai `sc_rnd`) và `supply_lead` (vai `sc_supply_lead`,
TP Cung ứng). BGĐ và Kế toán không có duty: họ quyết approval, giới hạn bằng
`required_scope` ([ADR 0020](0020-e10-approval-decider-stamped-as-required-scope.md)).

## Vì sao cùng context, và vì sao aggregate riêng

- Cùng ngôn ngữ (NCC, PIC, SKU, Category), cùng đội Cung ứng, và bàn giao ĐẶT HÀNG
  phải là một giao dịch. Context riêng sẽ cần Protocol, outbox cho bàn giao và một
  bản thứ hai của máy duty, approval, SLA.
- Không thêm trạng thái vào `POCase`: danh tính khác (chưa có số PO), vòng đời khác
  (vòng mẫu), và một sản phẩm có thể sinh nhiều PO (QE-12).

## Giả định chờ Elmich

- Vòng chỉnh sửa đi qua `revision_requested` rồi về `sample_testing`; "quay lại bước 2"
  và "lặp lại bước 3" được hiểu là cùng một vòng (QE-07). Không giới hạn số vòng.
- BGĐ không duyệt là hủy (QE-08).
- Bước 8 là xác nhận trong ứng dụng kèm file email, không đọc hộp thư (QE-09).

## Hệ quả

- Bước 12 (sơ đồ con thiết kế màu và bao bì) không thuộc ADR này; nó là một luồng
  con của Hồ sơ PO ở giai đoạn 2, chưa xếp ticket.
- Bảng mới: `product_dev_cases`, `product_dev_case_state_transitions`,
  `product_sample_rounds`, `sample_revision_requests`; mỗi bảng có `tenant_id`,
  `workspace_id`, RLS FORCE, và test âm xuyên tenant, xuyên workspace.

## Sửa đổi 2026-10-05 (tạm, lát S1; chờ Đạt duyệt ở QO-2)

Các quyết định tạm của lead khi làm lát S1 (ticket 01 giai đoạn 1). Chúng làm rõ phần
Quyết định ở trên; chi tiết và test ở Comments của
`.claude/plans/supply-chain/stage-1/issues/01-product-case-steps-1-5.md`.

1. **Duty là policy riêng.** `supply_chain_product_action_duties@1.0.0` (schema
   `SupplyChainProductActionDuties`, khóa theo `ProductAction`, đòi đủ mọi hành động), ghi
   đè qua cùng `PolicyOverridePort` như `supply_chain_action_duties`; không gộp vì năm tên
   hành động trùng với `CaseAction`. `propose`, `request_sample` thuộc `ordering`; năm bước
   R&D thuộc `rnd`; bốn bước ngoại lệ thuộc `exceptions`, `cancel` thuộc `ordering` như Hồ
   sơ PO. Mọi bước, kể cả `propose`, gác bằng scope duty của nó. Mở hồ sơ (`propose`) còn
   cần `supply_chain.product_case.write`, như `CreatePOCase` cần `po_case.write`; scope
   này cấp cho đúng các vai có `po_case.write` (hôm nay `sc_operator`) và nằm ở phía vận
   hành của `sod_sc_rules_vs_operations`. Đọc: `supply_chain.product_case.read` cho cả tám
   vai `sc_*`; vai mới `sc_rnd` = `sc_viewer` + duty `rnd` + tải chứng từ, KHÔNG có duty
   `exceptions` (duty đó dùng chung hai loại hồ sơ, có nó thì R&D tạm dừng và tiếp tục
   được Hồ sơ PO). Theo policy nền, bước ngoại lệ của hồ sơ phát triển do các vai vận hành
   PO làm; công ty muốn R&D làm thì ghi đè các bước đó sang `rnd` trong policy riêng này,
   không chạm Hồ sơ PO. `duty.rnd` ở phía vận hành của `sod_sc_rules_vs_operations`. Chưa
   có luật ordering-vs-R&D (QE-16). (Sửa 2026-10-06, review vòng 2: bản trước của đoạn
   này lệch quyết định 8 và 9 của lead; nay làm đúng chữ hai quyết định đó.)
2. **PIC** đóng dấu từ người gọi lúc `propose`; lệnh không có tham số PIC. Không có
   `reassign_pic` ở S1. Mọi người có quyền đọc thấy mọi hồ sơ của workspace; lọc PIC chỉ
   thu hẹp (QE-18).
3. **NCC** NULL lúc `propose`, bắt buộc và đóng dấu ở `request_sample` (bước 2, khi NCC
   được liên hệ). **Category** là text tự do không rỗng, đóng dấu lúc `propose`, chưa kiểm
   danh sách tới S6 (ADR 0019). **Mã đề xuất** duy nhất trong tenant; 409 ở workspace khác
   của cùng tenant xác nhận mã đã có, chấp nhận.
4. **Bốn bảng thu hẹp theo workspace** (dạng policy của `CLAUDE.md`), con tham chiếu hồ sơ
   bằng FK ghép có workspace, `ON DELETE CASCADE`. `product_dev_cases` giữ DELETE của
   `dw_app` như `po_cases`, vì purge của offboarding chỉ xóa bảng `dw_app` được DELETE và
   cascade từ hồ sơ là đường xóa các bảng con (đo: thiếu nó thì hồ sơ còn lại sau
   offboarding). Lịch sử và phiếu chỉnh sửa chỉ thêm; vòng mẫu chỉ được đóng một lần
   (UPDATE theo cột, trigger `closes_once`).
5. **Vòng mẫu** mở ở `receive_sample` và `receive_revised_sample`; `pass_sample`,
   `request_revision`, `reject_sample` đóng vòng hiện tại. Chứng từ của một vòng là chứng
   từ của chính hồ sơ (FK ghép tới `case_documents`), đúng loại, và tải lên từ khi vòng
   mở (`uploaded_at >= opened_at`); một biên bản chỉ đóng một vòng. Thiếu hay sai đều là
   409 nêu loại chứng từ thiếu. `cancel` khi mẫu đang test, hoặc tạm dừng lúc đang test,
   đóng vòng mở trong cùng bước với kết quả `rejected` (Hủy), nên hồ sơ đã hủy không còn
   vòng nào mở. Một bước nhận trường nó không dùng (NCC, chứng từ, lý do) thì từ chối.
6. **`pending_bod_review` là điểm dừng tạm ở S1:** không run approval nào. S2 đổi
   `pass_sample` để khởi động run bước 6 trong cùng lệnh, và phải xử lý các hồ sơ đã ở
   `pending_bod_review` từ trước S2 (backfill hoặc khởi động tường minh).
7. **Lịch sử** mỗi dòng có `action`, `actor_id`, `from_state` (NULL chỉ ở `propose`),
   `to_state`, `reason`, `occurred_at`; mọi lệnh ghi audit trong cùng giao dịch.

## Sửa đổi 2026-10-05 (tạm, lát S2; chờ Đạt duyệt ở QO-2)

Các quyết định tạm của lead khi làm lát S2 (ticket 02 giai đoạn 1, bước 6). Chi tiết và
test ở Comments của `.claude/plans/supply-chain/stage-1/issues/02-bod-review-step-6.md`.

1. **QE-08 (tạm):** `bod_reject` → `cancelled`, nhận xét của BGĐ là lý do (bắt buộc,
   tiền tố nghiêm đã đòi nhận xét). `bod_approve` → `profile_in_progress`, không có lý
   do; nhận xét nằm ở dòng quyết định của approval.
2. **Hành động chỉ graph áp:** `GRAPH_ONLY_ACTIONS = {bod_approve, bod_reject}` ở domain,
   một chủ. `available_actions` không bao giờ đưa ra; `AdvanceProductCase` từ chối
   trước khi đọc policy duty hay hồ sơ; model của route trả 422. Policy
   `supply_chain_product_action_duties` đòi duty cho mọi hành động TRỪ hai hành động
   này và từ chối khóa của chúng (không ai đọc khóa đó); override lưu trước S2 vẫn hợp lệ.
3. **Người quyết là actor:** dòng lịch sử, audit của `bod_approve`/`bod_reject` ghi
   người quyết, lấy từ `decided_by` mà `ApproveAndResumeService.decide` đặt vào payload
   resume (sửa đổi của ADR 0020). Đọc ghi DB vẫn trong tenant, workspace của run.
4. **Khởi động review:** `pass_sample` lưu trước (giao dịch riêng), rồi
   `EnsureBodReview.ensure` khởi động run. Idempotent nhờ seam
   `uq_worker_runs_active_thread`: thread tất định theo (hồ sơ, `bod_review`, vòng mẫu),
   run id mới mỗi lần; đụng thread là "đã có". Đã đo (LangGraph 1.2.11, saver bộ nhớ và
   saver Postgres): thread có run hỏng được gọi lại với input mới thì chạy lại từ START,
   interrupt cũ bị bỏ, quyết định tiếp tục đúng run mới. Đo cho cả hai dạng hỏng: dừng ở
   interrupt mà chưa ghi được approval, và `apply` hỏng SAU khi BGĐ đã quyết (quyết định
   cũ không được áp lại; review mới được trình, áp đúng một lần). "Đã trình" được đọc lại từ hộp
   approval, không tin `start()` trả về. Start bị từ chối (hết lượt chạy, trần chi tiêu)
   hoặc hỏng: bước vẫn lưu, phản hồi nói chưa trình được (`review: not_raised`), và lane
   worker `supply_chain_product_review_reconcile` trình lại. Lane đó cũng là backfill cho
   hồ sơ đã ở `pending_bod_review` trước S2; nó đi qua từng (tenant, workspace) có hồ sơ
   chờ BGĐ (hàm SECURITY DEFINER `supply_chain.workspaces_awaiting_bod_review()`, chỉ
   trả id), người yêu cầu là người làm bước đưa hồ sơ vào trạng thái chờ (đọc từ lịch sử),
   gói tính lượt là gói của tenant, tối đa 20 lần khởi động mỗi nhịp. Lần khởi động hỏng
   đầu tiên của một tenant (bị từ chối hoặc không trình được) kết thúc lượt của tenant đó
   trong nhịp, nên một tenant hết lượt chạy không chiếm hết nhịp của tenant sau nó.
5. **Trong lúc chờ BGĐ:** ở `pending_bod_review` người dùng chỉ hủy được; domain từ chối
   ba bước ngắt ở đó. Không có gì bên ngoài được chờ khi BGĐ đang quyết; báo ngoại lệ sau
   quyết định. Khi resume, graph đọc lại hồ sơ: không còn chờ đúng vòng này (đã hủy) thì
   không áp gì, ghi audit `bod_review_superseded`, run hoàn tất với outcome `superseded`.
   Approval của hồ sơ đã hủy vẫn nằm ở `/approvals` tới khi được quyết (mục mở).
6. **Policy người quyết:** `supply_chain_product_approvals@1.0.0`
   (`bod_review.required_scope: supply_chain.approve.bod`), schema
   `SupplyChainProductApprovals`, tenant ghi đè qua `PolicyOverridePort`, đọc lúc approval
   được tạo và đóng dấu lên dòng approval (cơ chế lát A). Chưa có route PUT. Dạng của
   scope vẫn chỉ do CHECK của cột quyết.
7. **Thông báo:** chỉ khi chính lần `ensure` đó tạo approval, trừ người yêu cầu; một
   thông báo trong ứng dụng, liên kết `/approvals`. Gửi hỏng thì ghi log, approval vẫn đó.
   Quyết định của lead nói "người giữ `supply_chain.approve.bod`"; code gửi cho người
   trong workspace của hồ sơ giữ CẢ scope đã đóng dấu lẫn `approvals.decide` (người không
   quyết được thì không được mời quyết). Phần thu hẹp này là **lệch của implementer, chờ
   lead hoặc Đạt xác nhận**, không phải bản thân quyết định.
8. **Vai `sc_bod`** = `sc_viewer` + `supply_chain.approve.bod` (migration
   `5857ae25747a`); `supply_chain.approve.bod` ở phía vận hành của
   `sod_sc_rules_vs_operations`. `approvals.decide` đến từ vai duyệt của nền tảng
   (`approver`, `manager`, `director`) hoặc permission set `approver_boost`: người BGĐ của
   Elmich giữ `sc_bod` cùng một trong số đó.
9. **Trang hồ sơ** hiện approval đang chờ (id, lúc tạo, scope đã đóng dấu) và liên kết
   `/approvals`; không tên người, không nút quyết. Đọc bằng `PendingApprovalsPort` (thêm
   đúng một phương thức), lọc theo workspace của người gọi.

## Sửa đổi 2026-10-07 (tạm, lát S3; Đạt ủy quyền quyết các điểm mở)

Các quyết định tạm khi làm lát S3 (ticket 03 giai đoạn 1, bước 7–8). Chi tiết và test ở
Comments của `.claude/plans/supply-chain/stage-1/issues/03-bm04-and-supplier-confirmation-steps-7-8.md`.

1. **Chứng từ của bước nằm trên dòng lịch sử.** `product_dev_case_state_transitions`
   thêm `document_id`, FK ghép `(tenant_id, workspace_id, product_dev_case_id,
document_id)` tới `case_documents`, `ON DELETE NO ACTION` như chứng từ của vòng mẫu,
   có index riêng; CHECK `ck_product_dev_case_state_transitions_document_steps`:
   `complete_profile` và `confirm_with_supplier` có chứng từ, mọi dòng khác không có
   (chứng từ của vòng mẫu vẫn nằm trên vòng). Migration `3fc6599ecd5e`.
2. **Mốc của chứng từ là lúc hồ sơ tới bước đó**, đọc từ lịch sử: dòng mới nhất vào trạng
   thái hiện tại mà không phải `resume` (tạm dừng rồi tiếp tục không tới lại bước, nên
   BM04 tải lên trước khi tạm dừng vẫn được). BM04 tải lên khi mẫu còn test bị từ chối.
   Domain `ProductDevelopmentCase._step_document` là chủ duy nhất; `action_options` trả
   cùng mốc (`documents_since`) cho trang, nên trang không giữ bản sao của luật.
3. **Policy duty 1.1.0** thêm `complete_profile: rnd`, `confirm_with_supplier:
supply_lead`. Override của tenant lưu ở 1.0.0 vẫn hợp lệ: hai bước thêm sau 1.0.0
   (`STEPS_ADDED_AFTER_1_0_0`) lấy duty của nền tảng khi override không nêu, mọi bước
   khác giữ lựa chọn của tenant; override 1.0.0 thiếu một bước ĐÃ có thì vẫn bị từ chối.
   Override ghi mới (PUT) và override khai 1.1.0 phải nêu đủ.
4. **Vai `sc_supply_lead`** (TP Cung ứng) = `sc_viewer` + `supply_chain.duty.supply_lead`
    - `supply_chain.document.write`, không có `duty.exceptions` (như `sc_rnd`). Duty ở phía
      vận hành của `sod_sc_rules_vs_operations`. Chưa có luật R&D–TP Cung ứng hay
      Cung ứng–TP Cung ứng (QE-16).
5. **Bước 7 do R&D** (duty `rnd`) dù bảng của Elmich ghi "R&D và Cung ứng": một người bấm,
   công ty muốn khác thì ghi đè trong policy. **Bước 8** giữ QE-09: xác nhận trong ứng
   dụng kèm file email (EML, MSG hoặc PDF theo danh sách của lát D), không đọc hộp thư.
6. **`item_coding`** ở S3 chỉ có bước ngoại lệ và hủy; mã hàng, SKU là S4. Cả ba trạng
   thái `profile_in_progress`, `supplier_confirmation`, `item_coding` tạm dừng và hủy
   được như các bước đang chạy khác.
7. **Downgrade từ chối** khi còn hồ sơ ở hoặc dừng từ hai trạng thái mới, dòng lịch sử
   nêu chúng, hoặc membership giữ `sc_supply_lead`.

## Sửa đổi 2026-10-07 (tạm, lát S4; Đạt ủy quyền quyết các điểm mở)

Các quyết định tạm khi làm lát S4 (ticket 04 giai đoạn 1, bước 9). Chi tiết và test ở
Comments của `.claude/plans/supply-chain/stage-1/issues/04-item-code-sku-signoff-step-9.md`.

1. **Trạng thái, hành động:** `pending_signoff`, `ready_to_order`; `issue_item_code`,
   `add_sku`, `remove_sku` (`item_coding` → `item_coding`, mỗi bước một dòng lịch sử, không
   dời mốc tới bước), `submit_for_signoff`, và hai hành động chỉ graph áp
   `signoff_approve`, `signoff_reject` (`GRAPH_ONLY_ACTIONS`). Ở `pending_signoff` chỉ hủy
   (`AWAITING_APPROVAL_STATES`, cùng luật với `pending_bod_review`); `ready_to_order` có
   bước ngoại lệ và hủy, ĐẶT HÀNG ở ticket 05. `product_dev_cases.signoff_round` đếm số lần
   trình; nó đặt tên vòng ký mà một quyết định thuộc về.
2. **QE-10 (tạm):** ký tuần tự theo `supply_chain_product_approvals@1.1.0` `signoff`
   (mặc định BGĐ rồi Kế toán, cả hai bắt buộc), mỗi bước một approval trong CÙNG một run của
   graph `supply_chain_product_signoff` (worker riêng, để run BGĐ đang chờ trên graph
   `supply_chain_advance_product_case` 1.0.0 giữ nguyên). Danh sách đọc lúc trình và đi
   theo run. Một bước không duyệt → `signoff_reject` → `item_coding`, nhận xét là lý do;
   mã hàng và SKU giữ nguyên; bước sau không được trình.
3. **Một hàm ensure:** `EnsureProductApproval` (đổi tên từ `EnsureBodReview`) trình
   approval mà hồ sơ đang chờ theo trạng thái (bảng `_WAITS`); lane
   `supply_chain_product_review_reconcile` (`ReconcileProductApprovals`) đi qua cả hai trạng
   thái chờ qua `supply_chain.workspaces_awaiting_product_approval()` và báo người quyết của
   mọi approval đang chờ (bước sau của vòng ký được tạo trong run, không ai được báo ở đó).
4. **Duty 1.2.0:** bốn bước của bước 9 thuộc `ordering`; override lưu ở 1.0.0 hoặc 1.1.0
   lấy duty nền tảng cho các bước thêm sau phiên bản của nó (`STEPS_ADDED_AFTER`).
