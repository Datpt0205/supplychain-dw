# 05 — ĐẶT HÀNG: bàn giao bước 9 → 10, `order_requested`, `create_po`

Status: resolved
Blocked by: .claude/plans/supply-chain/stage-1/issues/04-item-code-sku-signoff-step-9.md
Area: supply-chain

## Mục tiêu

Nút ĐẶT HÀNG tạo đúng một Hồ sơ PO chờ tạo PO, mang PIC, Category, SKU của sản phẩm;
bước 10 đặt số PO và loại đơn; từ đó bước 11–17 chạy như cũ. 17 bước thành một luồng
([ADR 0017](../../../../../docs/adr/0017-e7-hand-off-via-order-requested.md)).

## Việc cần làm

1. **Migration `po_cases`:** `po_reference` nullable với CHECK (khác NULL trừ
   `order_requested`); cột `product_dev_case_id` (FK `RESTRICT`, index), `order_kind`
   (CHECK `new | reorder`), `pic_user_id`, `category`. Bảng `po_case_lines`
   (`po_case_id` FK `CASCADE`, `sku_id` FK `RESTRICT`, cả hai có index;
   `quantity > 0`), RLS FORCE, grant. Dòng có sẵn: `order_kind` điền `reorder` (ghi lý
   do trong docstring migration).
2. **`CaseState.ORDER_REQUESTED`** đứng đầu luồng chính; `CaseAction.CREATE_PO` (duty
   `ordering`) đặt `po_reference`, `order_kind`, → `po_created`. Nhãn "Chờ tạo PO" vào
   `CASE_STATE_LABEL` và glossary. `CREATE_PO` có tham số nên **không đi qua
   `apply_action`**: một lệnh riêng `CreatePO(po_reference, order_kind)` gọi
   `POCase.create_po(...)`; `apply_action` từ chối `CREATE_PO` có tên, và một tập
   `_COMMAND_ONLY_ACTIONS` cạnh `_NO_REASON_ACTIONS` và `_REASON_ACTIONS`
   (`po_case.py:290`, `:306`) giữ tính đủ: mỗi `CaseAction` nằm đúng một trong ba tập (unit test).
3. **Bảng duty có phiên bản mới:** `supply_chain_action_duties@1.1.0.yaml` thêm
   `create_po: ordering`. `SupplyChainActionDuties._every_action_has_a_duty`
   (`action_duties.py:60-68`) từ chối tài liệu thiếu một `CaseAction`, nên mọi override
   đã lưu trong `platform.policy_overrides` sẽ hỏng và mọi `AdvancePOCase` của tenant đó
   lỗi. Một migration dữ liệu (mẫu `89e86dfabad6`) thêm `create_po: ordering` vào mọi
   override `supply_chain_action_duties` đã lưu, cùng thay đổi.
4. **`PlaceOrder`** (duty `ordering`), một giao dịch. Trước tiên câu cập nhật có điều
   kiện của ADR 0017 (`ready_to_order` → `ordered`, trạng thái mới, kết thúc; khớp cả
   `state` và `version` đã đọc); 0 dòng thì 409 và không chèn gì; rồi
   chèn `po_cases` (`order_requested`, chép PIC, Category, NCC từ chính dòng hồ sơ phát
   triển đọc trong giao dịch này) và `po_case_lines` (SKU, `planned_quantity` làm số
   lượng ban đầu, sửa được tới `create_po`); thông báo cho người giữ duty `ordering`.
   Câu cập nhật có điều kiện là thứ bảo đảm một PO, không phải UNIQUE (ADR 0017).
   Không ghi sự kiện outbox: chưa có consumer nào (failure-modes #1); thêm khi có
   consumer, với schema sự kiện có phiên bản.
5. **`create_po`** báo người giữ duty `finance` (Kế toán), theo cột "Bàn giao cho" của
   bước 10.
6. **`CreatePOCase`** (không qua giai đoạn 1) nhận `order_kind` bắt buộc,
   `product_dev_case_id` NULL, và đóng dấu `pic_user_id = context.principal_id`. Dòng có
   sẵn trước migration để `pic_user_id` NULL (S6 nói người nhận khi NULL).
7. **Mọi chỗ đọc `po_reference` chịu được NULL:** aggregate và repository, `ListPOCases`
   và bộ lọc, `case_query` (không bao giờ khớp NULL), daily brief và bộ kiểm câu tóm tắt,
   prompt `delay_impact_analysis`, contracts TypeScript (`string | null`), trang danh
   sách và chi tiết ("Chưa có số PO"), fixture và expected của eval.
8. **`order_requested` ở các chỗ đọc theo trạng thái**, mỗi chỗ một unit test:
    - `missing_update.py:74` coi mọi trạng thái không kết thúc là tới hạn nhắc cập nhật
      NCC; `order_requested` bị loại (chưa có PO thì chưa có NCC để hỏi);
    - đánh giá SLA trả `not_applicable` ở `order_requested` (mốc riêng là việc sau, khi
      Elmich trả lời QE-01);
    - daily brief và hàng chú ý: hồ sơ `order_requested` hiện thành nhóm "Chờ tạo PO"
      cho người giữ duty `ordering`, không vào nhóm thiếu cập nhật;
    - ma trận approval mặc định không liệt kê `create_po`.
9. **Test đầu-cuối** (integration, qua handler): một sản phẩm đi bước 1 → `ordered` →
   `create_po` → bước 11–17 → `completed`, với approval quyết bởi người dùng thử đúng vai.

## Tiêu chí chấp nhận

- [x] Hai `PlaceOrder` song song cho cùng hồ sơ (hai giao dịch thật trên Postgres): một
      Hồ sơ PO; lần hai 409. Mutation: bỏ `AND version = :v AND state = 'ready_to_order'`
      khỏi câu cập nhật thì test đỏ (ghi vào Comments).
- [x] Chèn thẳng `po_cases` ở `po_created` với `po_reference` NULL (vai `dw_app`) bị
      CHECK từ chối.
- [x] PIC là dấu: sau `PlaceOrder`, `po_cases.pic_user_id` bằng `pic_user_id` của dòng
      hồ sơ phát triển đọc trong cùng giao dịch; không chỗ đọc nào của Hồ sơ PO join lại
      `product_dev_cases` để lấy PIC (test hoặc grep trong CI). Đổi PIC sau ĐẶT HÀNG được
      kiểm ở S6, nơi `reassign_pic` ra đời.
- [x] Một override `supply_chain_action_duties` lưu trước migration vẫn nạp được sau
      migration và có `create_po: ordering`; `AdvancePOCase` của tenant đó chạy.
- [x] `apply_action(CREATE_PO)` bị từ chối có tên; unit test tính đủ của ba tập hành
      động đỏ khi thêm một `CaseAction` mà không xếp vào tập nào.
- [x] `CreatePOCase` đóng dấu `pic_user_id` là người gọi.
- [x] **Test âm:** `PlaceOrder` trên hồ sơ tenant khác hoặc workspace khác trả 404; RLS
      của `po_case_lines` chặn tenant B; `test_rls_coverage.py` xanh.
- [x] Unit cho từng chỗ đọc ở bước 6 với `po_reference` NULL; eval dataset hiện có vẫn
      xanh.
- [x] Test đầu-cuối ở bước 7 xanh.
- [x] Mutation: bỏ CHECK trong migration thì test chèn thẳng đỏ (ghi vào Comments).
- [x] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 9–10; mục 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3, "Hand-off at ĐẶT HÀNG"; mục 2 hàng 10.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "Hand-off".

## Comments

- Một hồ sơ phát triển sinh mấy PO, và Hàng mới có được tạo không qua giai đoạn 1, chờ
  QE-12; thiết kế không chặn trường hợp nào.

- 2026-10-07 (lát S5, quyết định tạm của implementer; Đạt ủy quyền quyết các điểm mở,
  "an toàn nhất"). Đoạn sửa đổi ở
  [ADR 0017](../../../../../docs/adr/0017-e7-hand-off-via-order-requested.md) (mục "Sửa
  đổi 2026-10-07, lát S5"). Mỗi điểm ghi chỗ nó nằm trong code và test giữ nó.
    1. **QE-12 (tạm): một hồ sơ phát triển, một Hồ sơ PO.** Câu cập nhật có điều kiện của
       ADR 0017 (`SqlProductCaseRepository.place_order`: `WHERE id AND version = :v AND
       state = 'ready_to_order'`, cùng tenant/workspace) là thứ bảo đảm; **lệch chữ ADR**
       ("không UNIQUE"): thêm UNIQUE `uq_po_cases_tenant_id_workspace_id_product_dev_case_id`
       làm câu trả lời thứ hai của database (như ADR 0018), cũng là index của FK. Elmich trả
       lời "nhiều" thì bỏ ràng buộc bằng migration mới và đổi máy trạng thái. Hàng đặt lại
       không qua giai đoạn 1 (`CreatePOCase`, `order_kind` bắt buộc, `product_dev_case_id`
       NULL). Test `test_two_clicks_at_once_open_one_po_case` (hai giao dịch thật, cả hai
       đọc trước khi ghi nhờ `asyncio.Barrier`; người thua nhận 409 từ câu cập nhật, không
       phải từ UNIQUE), `test_an_order_on_a_case_cancelled_meanwhile_opens_nothing`,
       `test_a_second_po_case_for_one_product_case_is_refused`,
       `test_a_second_click_is_409_and_a_replay_under_the_same_key_is_the_first_answer` (API).
    2. **`po_reference` do Cung ứng đặt ở bước 10** (`CreatePO`, duty của `create_po` trong
       policy duty PO 1.1.0, mặc định `ordering`). CHECK `ck_po_cases_po_reference`: NULL chỉ
       ở `order_requested` hoặc `cancelled` (hủy khi đang chờ tạo PO), khác rỗng ở mọi chỗ
       khác. Số PO trùng trong tenant là 409 nêu ràng buộc và số (cả `CreatePOCase`, trước
       là 500). Test `test_a_po_case_past_order_requested_without_a_reference_is_refused`,
       `test_create_po_sets_the_reference_and_a_taken_one_is_a_409_naming_it`.
    3. **`CREATE_PO` không qua `apply_action`**: `_COMMAND_ONLY_ACTIONS` cạnh
       `_NO_REASON_ACTIONS`, `_REASON_ACTIONS`; `apply_action` từ chối có tên; route bước
       của Hồ sơ PO trả 422; ma trận approval từ chối `create_po` (không ai đọc). Cùng
       cách cho `place_order` ở hồ sơ phát triển (`COMMAND_ONLY_ACTIONS`): `AdvanceProductCase`
       từ chối trước khi đọc gì, route bước 422. Test
       `test_every_action_is_in_exactly_one_dispatch_set`,
       `test_apply_action_refuses_create_po_by_name`, `test_a_plain_po_step_cannot_be_create_po`,
       `test_the_step_command_refuses_place_order_before_reading_anything`,
       `test_a_matrix_gating_create_po_is_refused`.
    4. **Ở `order_requested` chỉ `create_po` hoặc hủy**; không tạm dừng (chưa có PO thì
       không có gì bên ngoài để chờ). SLA `not_applicable`; không bao giờ tới hạn nhắc NCC;
       daily brief: nhóm `waiting_on_us:order_requested` ("Chờ tạo PO"), không thêm tín
       hiệu mới (thêm thì mọi override `supply_chain_brief` đã lưu hỏng). **Lệch nhẹ chữ
       ticket:** nhóm hiện cho mọi người đọc được brief, như mọi nhóm; người giữ duty được
       báo bằng thông báo. Không vào hàng chú ý (không trễ SLA, không thiếu cập nhật).
       Test `test_a_case_awaiting_its_po_*`, `test_the_brief_groups_cases_awaiting_their_po_*`.
    5. **Dòng PO**: `quantity` NULL hoặc > 0 (lệch chữ "`quantity > 0`" của ticket):
       `planned_quantity` của SKU có thể trống (QE-11), nên số lượng mở tới bước 10;
       `create_po` nhận số lượng các dòng (sửa được tới đây) và từ chối 409 nêu SKU khi còn
       dòng trống. FK tới Hồ sơ PO `CASCADE`, tới SKU `RESTRICT`, cả hai ghép với workspace
       (thêm UNIQUE `(tenant_id, workspace_id, id)` cho `skus`). RLS FORCE dạng workspace.
       `dw_app`: SELECT, INSERT, UPDATE `quantity`; không DELETE. **Mục mở:** `po_cases` vẫn
       chỉ hẹp theo tenant (từ `fddd7579ba27`); workspace khác cùng tenant thấy Hồ sơ PO,
       không thấy dòng. Test `test_another_tenant_or_workspace_neither_reads_nor_writes_the_lines`,
       `test_the_application_adds_lines_and_sets_their_quantity_only`,
       `test_create_po_needs_a_quantity_on_every_line`.
    6. **PIC là dấu**: `ProductDevelopmentCase.place_order` dựng Hồ sơ PO từ chính dòng đọc
       trong lệnh, PIC/Category/NCC của hồ sơ, không phải của người bấm; repository Hồ sơ PO
       không nhắc tới `product_dev_cases` (`test_no_po_case_read_joins_the_product_case`).
       `PlaceOrder.handle` chỉ có `context`, `case_id`; route nhận body rỗng hoặc không body,
       `pic_user_id`/`category`/`supplier_name` là 422 (test đỏ trước khi thêm model
       `extra="forbid"`: FastAPI bỏ qua body không khai báo). `CreatePOCase` đóng dấu người
       gọi. Test `test_placing_the_order_opens_the_po_case_with_the_rows_stamps_in_one_transaction`,
       `test_the_order_route_takes_nothing_from_the_request` (3),
       `test_create_po_case_stamps_its_caller_as_pic_and_takes_the_kind`.
    7. **Thông báo**, sau khi ghi (hỏng thì log, việc vẫn còn): ĐẶT HÀNG báo người giữ duty
       mà policy PO của tenant giao cho `create_po` (một chủ, không viết cứng `ordering`), trừ
       người bấm; `create_po` báo người giữ duty `finance` (cột "Bàn giao cho" của bước 10),
       trừ người làm. Test `test_the_ordering_duty_is_told_there_is_a_po_to_create`
       (integration, thành viên thật, workspace khác không được báo),
       `test_who_is_told_follows_the_tenants_duty_for_create_po`,
       `test_a_failed_notice_does_not_undo_the_order`.
    8. **Audit trong cùng giao dịch:** `supply_chain.product_case.place_order` và
       `supply_chain.po_case.order_requested` (ĐẶT HÀNG), `supply_chain.po_case.create_po`
       (bước 10; `POCaseRepositoryPort.save` nhận `audit`). Bước 11–17 vẫn chưa ghi audit (có
       từ trước, ngoài lát).
    9. **Policy:** duty hồ sơ phát triển 1.3.0 (`place_order: ordering`, `STEPS_ADDED_AFTER`
       cho override 1.0.0–1.2.0); duty PO 1.1.0 (`create_po: ordering`) và migration dữ liệu
       thêm khóa vào mọi override đã lưu (mẫu `89e86dfabad6`). Test
       `test_an_override_stored_before_create_po_loads_after_the_migration` (chạy đúng câu SQL
       của migration trên một override lưu như trước 1.1.0; trước câu SQL `AdvancePOCase` hỏng
       vì schema, sau đó chạy, lựa chọn của tenant giữ nguyên).
    10. **Phòng thủ chiều sâu:** `_case_in_workspace` của hồ sơ phát triển so cả tenant (trước
        chỉ workspace); test `test_another_tenant_or_workspace_cannot_order_the_case` dùng
        repository "quên" lọc.
    11. **Web (antd, E-HSDT v3):** nút ĐẶT HÀNG (khóa có lý do bằng chữ khi thiếu duty hoặc
        `unmet`, `modal.confirm`, một `Idempotency-Key` mỗi lần thử, 409 hiện câu của server);
        hồ sơ đã đặt hàng liên kết Hồ sơ PO; trang Hồ sơ PO: "Chưa có số PO", loại đơn,
        Category, PIC, liên kết về hồ sơ phát triển, bảng dòng PO, form "Tạo PO (bước 10)"
        (số PO trùng hiện tại trường). Form không khóa theo vai: trang Hồ sơ PO không có
        `required_scope` và duty của `create_po` là policy của tenant; server quyết, 403 hiện
        câu của nó. Một helper `poReferenceLabel` cho mọi chỗ hiện số PO. Không có form tạo
        Hồ sơ PO trên web nên không thêm lời gọi `order_kind` không ai dùng.
    12. **Migration** `84d1c1946b44` (id ngẫu nhiên, down_revision `76bd1b5fc546`, một head).
        Dòng có sẵn `order_kind = 'reorder'` (đều do `CreatePOCase` mở), rồi bỏ default. Đã
        chạy upgrade → downgrade -1 → upgrade trên DB local. Downgrade từ chối khi còn dòng
        cần phần nó gỡ.
    13. Không ghi sự kiện outbox (chưa có consumer, failure-modes #1).
- 2026-10-07 (lát S5) **Mutation** (`mutate.py` tạm, mỗi lần sửa một guard, chạy test của
  nó, khôi phục; không áp được thì báo, không tính là bắt được): 20/20 đỏ. M1 bỏ `AND
  version = :v AND state = 'ready_to_order'` khỏi câu cập nhật: đỏ
  `test_an_order_on_a_case_cancelled_meanwhile_opens_nothing` (PO mở trên hồ sơ đã hủy) và
  `test_two_clicks_at_once_open_one_po_case` (người thua bị UNIQUE từ chối thay vì câu cập
  nhật); M4 PlaceOrder bỏ kiểm duty; M5 PIC lấy từ người bấm; M7 migration dữ liệu không
  thêm gì; M8 `apply_action` dispatch `create_po`; M9 lệnh bước để `place_order` đi tới chỗ
  đọc; M10 route bỏ body `extra="forbid"`; M11 nhắc NCC cho hồ sơ chờ tạo PO; M12 brief mất
  nhóm Chờ tạo PO; M13 CreatePO bỏ kiểm duty; M14 ma trận approval nhận `create_po`; M15
  `create_po` khi còn dòng trống; M16 handler không so tenant; M18 người bấm tự được báo;
  M19 tạm dừng được ở `order_requested`; M20 số PO trùng không ánh xạ (500). Bốn guard của
  database đột biến **trong text migration** (bộ integration dựng DB test mới từ migration
  mỗi phiên; lần thử đầu sửa thẳng DB dev, bộ test không dùng DB đó, 3 "xanh" — không tính):
  D1 bỏ UNIQUE một PO mỗi hồ sơ, D2 bỏ CHECK `ck_po_cases_po_reference`
  (`test_a_po_case_past_order_requested_without_a_reference_is_refused` đỏ), D3 bỏ ENABLE/FORCE
  RLS của `po_case_lines`, D4 policy dòng chỉ theo tenant.
- 2026-10-07 (lát S5) **Security review** (`reviewing-feature-security`): (1) cách ly tenant:
  `po_case_lines` RLS FORCE dạng workspace, mọi câu chạy trong `tenant_session` theo giao dịch,
  test âm tenant/workspace khác (đọc 0 dòng, ghi bị WITH CHECK từ chối, ĐẶT HÀNG 404); không
  cache, không object key, không Qdrant mới. (2) Phân quyền: duty kiểm ở handler trước khi đọc,
  PIC/Category/NCC từ dòng của server, route từ chối mọi trường. (3) Autonomy: không có tool
  agent mới, không run mới. (4) Nội dung không tin cậy: không gọi model mới; số PO người gõ
  chỉ hiện như chữ trong thông báo, prompt `delay_impact_analysis` nhận nhãn "Chưa có số PO"
  thay vì `None`. (5) Audit cùng giao dịch cho ĐẶT HÀNG và `create_po`; FK `RESTRICT` từ Hồ sơ
  PO tới hồ sơ phát triển và từ dòng tới SKU. (6) Vòng đời: không tài nguyên mới; thông báo
  hỏng không hoàn tác việc đã ghi (không phải quyết định quyền). Không Dockerfile, lockfile
  hay image đổi (configs được COPY cả thư mục).
- 2026-10-07 (lát S5) **Kiểm tra đã chạy** trên `feat/elmich-a-d-s1`: `make lint` xanh (chỉ
  cảnh báo eslint cũ ở `components/assistant-ui/*`); `make typecheck` xanh (mypy 521 file,
  tsc 4 gói); `make test-unit` 2620 passed, 3 skipped; `make test-architecture` (import-linter
  9 kept, declared-dependency 12 gói, `verify_invariants.py` ok); `make test-contract` 5
  passed; `make eval-smoke` platform 4/4, supply_chain 30/30 (dataset không đổi); `make
  release-manifest-check` OK `sha256:d56990955b94…` (history thêm `manifest-d56990955b94.json`);
  `make test-hooks` 76 passed; integration (Postgres thật của repo này): `dw_supply_chain`
  274, `dw_platform` 257, `dw_agent_runtime` 92, `apps/worker` 44, tất cả passed; vitest toàn
  bộ `@dw/web` 336 passed (32 file, 21 ca mới); `make generate-contracts` sinh lại
  `openapi.json` và `supply-chain.d.ts` (đổi về LF). Test đầu-cuối
  `test_one_product_from_step_one_to_its_po_case_completed` xanh.
