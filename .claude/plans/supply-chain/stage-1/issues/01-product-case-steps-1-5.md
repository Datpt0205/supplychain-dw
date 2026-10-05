# 01 — Hồ sơ phát triển sản phẩm: bước 1–5 (đề xuất, lấy mẫu, test, chỉnh sửa)

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/case-documents/issues/01-case-documents.md
Area: supply-chain

## Mục tiêu

Cung ứng đề xuất một sản phẩm và trở thành PIC của nó; R&D nhận mẫu, test, yêu cầu
chỉnh sửa hoặc hủy, tới khi mẫu đạt và hồ sơ chờ BGĐ duyệt
([ADR 0016](../../../../../docs/adr/0016-e6-stage-1-is-a-product-development-case-inside-dw-supply-chain.md)).

## Việc cần làm

1. **Domain** `domain/product_development_case.py`: `ProductDevelopmentCase` (id,
   tenant, workspace, `proposal_code`, `product_name`, `category`, `supplier_name`,
   `pic_user_id`, `state`, `interrupted_state`, `sample_round`, `version`,
   `created_by`, `created_at`); `ProductDevState` chỉ gồm trạng thái lát này với tới:
   `proposed`, `sample_requested`, `sample_testing`, `revision_requested`,
   `pending_bod_review`, `cancelled`, cùng `waiting_external`, `blocked`,
   `manual_review`. `ProductAction`: `propose`, `request_sample`, `receive_sample`,
   `pass_sample`, `request_revision`\*, `receive_revised_sample`, `reject_sample`\*,
   `wait_for_external`\*, `flag_blocked`\*, `flag_manual_review`\*, `resume`, `cancel`\*
   (\* bắt buộc lý do). Một dispatch `apply_product_action`, theo mẫu `apply_action`
   của `POCase`.
2. **PIC:** `pic_user_id = context.principal_id` lúc `propose`; schema request không có
   trường PIC (`extra="forbid"`).
3. **Migration** (id hex ngẫu nhiên, schema `supply_chain`): `product_dev_cases`
   (UNIQUE `(tenant_id, proposal_code)`; index cho danh sách keyset bắt đầu bằng
   `tenant_id`), `product_dev_case_state_transitions` (from, to, action, reason,
   actor, occurred_at; index `(tenant_id, occurred_at)`), `product_sample_rounds`
   (UNIQUE `(product_dev_case_id, round_no)`; `result` CHECK
   `passed | needs_revision | rejected`; FK biên bản tới `case_documents`),
   `sample_revision_requests` (round_no, requested_changes, sent_by, sent_at, FK phiếu
   tới `case_documents`). Mọi FK có `ON DELETE` và index; RLS FORCE hình workspace
   chuẩn; grant trong migration. `case_documents` thêm `product_dev_case_id` và CHECK
   đúng một FK khác NULL.
4. **Chứng từ bắt buộc:** `pass_sample` cần một `sample_evaluation` của vòng hiện tại;
   `request_revision` cần một `sample_revision_request` (Phiếu yêu cầu chỉnh sửa).
   Chứng từ phải thuộc đúng hồ sơ này. `propose` nhận tùy chọn một hoặc nhiều
   `product_image` (ảnh SP, đầu ra của bước 1); không bắt buộc, vì danh sách SP đề xuất
   có thể chưa có ảnh (QE-02 nói tài liệu nào bắt buộc ở bước nào).
5. **Duty:** policy duty (phiên bản mới, override cũ vẫn hợp lệ) gán hành động của
   Cung ứng cho `ordering`, của R&D cho `rnd`; scope `supply_chain.duty.rnd`; vai
   `sc_rnd` (chứa `sc_viewer`) bằng migration `platform.roles`; `test_role_catalogue.py`
   cập nhật.
6. **Handler và route:** `POST /product-cases` (`Idempotency-Key`), `GET /product-cases`
   (keyset, lọc theo trạng thái, PIC), `GET /product-cases/{id}`,
   `POST /product-cases/{id}/actions`, `GET /product-cases/{id}/transitions`,
   `POST`/`GET /product-cases/{id}/documents` (dùng lại handler của lát D).
7. **Web (antd):** `apps/web/app/supply-chain/product-cases/` danh sách và chi tiết
   (trạng thái, PIC, vòng mẫu, lịch sử, chứng từ, nút hành động theo duty, khóa kèm lý
   do khi thiếu duty); một bảng nhãn trạng thái duy nhất theo glossary; mục nav.

## Tiêu chí chấp nhận

- [x] Unit domain: mọi chuyển hợp lệ; mọi chuyển sai trạng thái bị từ chối; hành động
      \* thiếu lý do bị từ chối; vòng chỉnh sửa tăng `sample_round`; ngắt rồi `resume`
      trả về trạng thái trước.
- [x] PIC: body gửi kèm `pic_user_id` bị 422; PIC là người tạo.
- [x] `pass_sample` thiếu biên bản, hoặc biên bản của hồ sơ khác cùng tenant: 409 nêu
      loại chứng từ thiếu.
- [x] Duty: `sc_operator` không `pass_sample` được; `sc_rnd` không `propose` được (403).
- [x] **Test âm RLS** cho bốn bảng mới: tenant B không đọc, không ghi; workspace khác
      không đọc; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [x] **Test âm route:** `GET`, `POST actions`, `documents` trên hồ sơ của tenant khác
      hoặc workspace khác trả 404.
- [x] Trùng `proposal_code` trong tenant: 409 theo tên ràng buộc; cùng mã ở tenant khác
      được.
- [x] Mutation: bỏ kiểm chứng từ ở `pass_sample` thì test đỏ; bỏ đóng dấu PIC thì test
      đỏ (ghi vào Comments).
- [x] Vitest trang danh sách và chi tiết (trạng thái rỗng, lỗi, nút khóa có lý do).
- [x] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 1–5; mục 5 điểm 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3 ("Entities", bảng trạng thái hàng 1–7, "PIC rule").
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4 ("New tables", "State machine", "PIC").
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, "The dw_supply_chain package" (mẫu `POCase`, `apply_action`).

## Comments

- Giả định: vòng chỉnh sửa là `revision_requested` → `sample_testing` (QE-07); không
  giới hạn số vòng.

### 2026-10-05 — lát S1 đã làm (implementer); quyết định tạm của lead, chờ Đạt duyệt ở QO-2

**Trạng thái:** mọi tiêu chí chấp nhận đạt (bảng kiểm tra cuối mục). Status `resolved`.
Không commit; nhánh `feat/elmich-a-d-s1`. Head migration duy nhất `7c422b849fe9`
(`f6a8142a6cd2` của D → `59e69efdfa37` → `7c422b849fe9`).

**Quyết định tạm của lead, mỗi cái đã làm như sau** (ADR 0016 và ADR 0021 có đoạn sửa đổi
2026-10-05, gắn cờ QO-2):

1. **Workspace.** Bốn bảng `product_dev_cases`, `product_dev_case_state_transitions`,
   `product_sample_rounds`, `sample_revision_requests`: RLS ENABLE + FORCE, policy
   `tenant AND (workspace OR app.workspace_scope = 'tenant')` ở USING và WITH CHECK. Con
   tham chiếu hồ sơ bằng FK ghép `(tenant_id, workspace_id, product_dev_case_id)` tới
   `product_dev_cases (tenant_id, workspace_id, id)`
   (`uq_product_dev_cases_tenant_id_workspace_id_id`), `ON DELETE CASCADE`. Grant của
   `dw_app`: lịch sử và phiếu chỉnh sửa chỉ SELECT, INSERT; vòng mẫu SELECT, INSERT và
   UPDATE theo cột (điểm 6). **Lệch so với quyết định:** `product_dev_cases` giữ DELETE mặc
   định của schema (như `po_cases`), không chỉ SELECT/INSERT/UPDATE. Lý do: purge của
   offboarding chỉ xóa bảng `dw_app` được DELETE
   (`SqlTenantOffboarding._CATALOG_PURGEABLE`), và cascade từ hồ sơ là đường xóa mọi bảng
   con. Đã đo: thu DELETE như quyết định viết thì
   `test_offboarding_purges_product_cases_rounds_and_documents_of_one_tenant` đỏ
   (`assert 2 == 0`: hai hồ sơ của tenant đã offboard còn lại). Không đường code nào xóa
   hồ sơ. Offboarding mở rộng trong chính test đó: tenant có hồ sơ ở hai workspace, mỗi
   hồ sơ có lịch sử, vòng đạt trên biên bản, phiếu chỉnh sửa và chứng từ; tenant khác giữ
   nguyên.
2. **`proposal_code`** UNIQUE `(tenant_id, proposal_code)`
   (`uq_product_dev_cases_tenant_id_proposal_code`); adapter đổi thành 409 theo tên ràng
   buộc (`details.constraint`). **Rò rỉ chấp nhận, ghi lại:** ở workspace khác cùng
   tenant, 409 xác nhận một mã đã có (không lộ gì khác).
3. **Category** text tự do, cắt khoảng trắng, CHECK `btrim(category) <> ''` (tối đa 100
   ký tự), đóng dấu lúc `propose`; chưa kiểm danh sách tới S6 (ADR 0019). Không có mức
   ưu tiên (QE-13).
4. **`supplier_name`** NULL lúc `propose`; `request_sample` bắt buộc NCC không rỗng trong
   body và đóng dấu. Lý do: bước 1 của process.md chỉ ra mã, tên SP và ảnh; NCC được liên
   hệ ở bước 2.
5. **PIC** `pic_user_id NOT NULL` = `context.principal_id` lúc `propose`.
   `ProposeProductCase.handle` không có tham số PIC nào (test
   `test_the_propose_command_takes_no_pic_at_all` kiểm chữ ký), request model
   `extra="forbid"` (body có `pic_user_id` thì 422). Không có `reassign_pic` ở S1. Mọi
   người có `supply_chain.product_case.read` thấy mọi hồ sơ của workspace; lọc PIC chỉ thu
   hẹp (QE-18).
6. **Vòng mẫu.** Dòng vòng mở ở `receive_sample` (vòng 1) và `receive_revised_sample`
   (vòng n+1), `result` NULL; CHECK `result IS NULL OR IN (passed, needs_revision,
rejected)`; UNIQUE `(tenant_id, product_dev_case_id, round_no)`, thêm UNIQUE riêng phần
   "một vòng mở mỗi hồ sơ". `pass_sample`, `request_revision`, `reject_sample` đóng vòng
   bằng UPDATE có canh `WHERE result IS NULL`; trigger `closes_once` (tên
   `ck_product_sample_rounds_closes_once`) từ chối mọi UPDATE của vòng đã đóng; `dw_app`
   chỉ được UPDATE bốn cột `result, evaluation_document_id, closed_at, closed_by`
   (`test_privileges.py::test_the_application_may_only_close_a_sample_round`). Biên bản:
   `evaluation_document_id` phải là `case_documents` của CHÍNH hồ sơ (FK ghép
   `(tenant_id, workspace_id, product_dev_case_id, evaluation_document_id)` tới
   `case_documents (tenant_id, workspace_id, product_dev_case_id, id)`), loại
   `sample_evaluation`, UNIQUE `(evaluation_document_id)`; CHECK vòng `passed` phải có biên
   bản. **Luật "biên bản của vòng hiện tại" đã chọn:** `uploaded_at >= opened_at` của vòng
   hiện tại (cả hai là `now()` của database); vòng vừa mở trong bộ nhớ, chưa lưu, không
   nhận chứng từ nào. Cùng luật cho Phiếu yêu cầu chỉnh sửa và biên bản tùy chọn của
   `reject_sample`. Luật nằm ở domain (`ProductDevelopmentCase._round_document`), một
   chủ. Thiếu, của hồ sơ khác, không đọc được (tenant hoặc workspace khác), sai loại, hoặc
   của vòng trước: cùng một 409 `details.missing_document_type`, không nói là trường hợp
   nào. (Sửa ở review vòng 1: trang chi tiết giữ một bản sao trình bày của luật này để
   thu hẹp danh sách chọn; xem điểm 16.) `cancel` khi mẫu đang test, hoặc tạm dừng lúc
   đang test, đóng vòng mở với `rejected` trong cùng bước (review vòng 1). Test: domain `test_a_round_one_report_cannot_pass_round_two`; integration
   `test_a_round_one_report_is_refused_for_round_two_by_its_upload_time`,
   `test_a_round_cannot_be_passed_on_another_cases_evaluation` (FK),
   `test_one_evaluation_cannot_close_two_rounds`,
   `test_a_round_closes_once_whatever_the_statement_says`. Phiếu:
   `sample_revision_requests` lưu `revision_document_id` (FK ghép như trên, UNIQUE),
   `round_no` (FK tới vòng), `requested_changes` (lý do của `request_revision`),
   `sent_by`, `sent_at`.
7. **Duty.** Policy riêng `configs/policies/supply_chain_product_action_duties@1.0.0.yaml`,
   schema `SupplyChainProductActionDuties` (`dw_supply_chain/product_action_duties.py`,
   khóa theo `ProductAction`, bắt buộc mọi hành động), giải qua `PolicyOverridePort` bằng
   `resolve_product_action_duties` (cùng `_resolve_policy` của PO); override PO không bị
   chạm (`test_a_tenant_override_moves_a_step_and_a_po_override_does_not`). Override hỏng
   thì từ chối, không rơi về mặc định
   (`test_a_broken_tenant_override_refuses_rather_than_falling_back`). **Route admin đã
   thêm** (nhỏ, soi gương action-duties): `GET`/`PUT
/api/v1/supply-chain/product-action-duties`, dùng lại scope
   `supply_chain.action_duties.read/write`. `ProductAction` là enum riêng.
   `CaseDuty.RND = "rnd"`, scope `supply_chain.duty.rnd`. Ánh xạ: `propose`,
   `request_sample` thuộc `ordering`; năm bước R&D thuộc `rnd`; `wait_for_external`,
   `flag_blocked`, `flag_manual_review`, `resume` thuộc `exceptions`, `cancel` thuộc
   `ordering`, đúng như PO (`test_the_exception_steps_mirror_the_po_case`). Thêm `RND`
   không làm hỏng validator của PO (nó đòi mọi `CaseAction` có duty, không đòi mọi duty có
   hành động): không cần data migration. Release manifest đã tạo lại
   (`sha256:af070fd3…`).
8. **Vai.** Migration `7c422b849fe9`: `supply_chain.product_case.read` vào cả bảy vai
   `sc_*`; `sc_rnd` = toàn bộ scope hiện có của `sc_viewer` (đọc từ dòng, nên có cả scope
   đọc mà migration sau thêm) cộng `duty.rnd`, `document.write`, đúng cách `_duty_role`
   dựng nhưng **không** có `duty.exceptions` (sửa ở review vòng 2, xem dưới);
   `duty.rnd` vào phía vận hành của `sod_sc_rules_vs_operations`. Không luật
   ordering-vs-R&D (QE-16).
   `test_role_catalogue.py`: `_OPERATING_ROLES` có `sc_rnd`; thêm
   `test_every_role_reads_product_cases`, `test_rnd_tests_samples_and_does_not_order`, cặp
   không xung đột `(sc_rnd, sc_operator)`, `(sc_rnd, sc_qc)`; cặp
   `(sc_process_admin, sc_rnd)` tự vào bộ tham số SoD; vẫn đúng 5 luật `sod_sc_`.
   (Bản 5/10 cho `sc_rnd` thêm `duty.exceptions`, lệch quyết định 8; review vòng 2 đã bỏ.
   Xem mục 2026-10-06 vòng 2.)
9. **Scope.** `PRODUCT_CASE_READ = "supply_chain.product_case.read"` và
   `PRODUCT_CASE_WRITE = "supply_chain.product_case.write"` trong
   `application/handlers.py`. Mẫu PO có scope mở hồ sơ (`CreatePOCase` gác bằng
   `po_case.write`), nên theo quyết định 9 `ProposeProductCase` gác bằng
   `product_case.write`; `propose` cũng là một bước của policy (điểm 7) nên hỏi thêm duty
   của nó. Các bước khác gác bằng scope duty của bước. (Bản 5/10 không có scope ghi, lệch
   quyết định 9; review vòng 2 đã thêm. Xem mục 2026-10-06 vòng 2.)
10. **Lịch sử** có `action`, `from_state` (NULL chỉ ở `propose`, có CHECK), `to_state`,
    `reason`, `actor_id`, `occurred_at`; bước chờ lưu của aggregate mang action và actor.
    Index `(tenant_id, product_dev_case_id, occurred_at)` theo lead (thay
    `(tenant_id, occurred_at)` của ticket). Danh sách keyset có
    `ix_product_dev_cases_page (tenant_id, created_at DESC, id DESC)` và hai index lọc
    (trạng thái, PIC) cùng dạng.
11. **Route** soi gương PO, router riêng mount theo guard riêng
    (`presentation/product_case_routes.py`): `POST /product-cases` (Idempotency-Key),
    `GET /product-cases` (keyset, `state`, `pic_user_id`), `GET /product-cases/{id}`,
    `POST /product-cases/{id}/transitions` (Idempotency-Key; body `action`, `reason?`,
    `supplier_name?`, `document_id?`; thay `/actions` của ticket theo lead),
    `GET /product-cases/{id}/transitions`, `POST`/`GET /product-cases/{id}/documents` qua
    CHÍNH handler của D, tổng quát theo `CaseKind` (`cases: Mapping[CaseKind,
CaseLookupPort]`, port hỏi `case_workspace`; route đăng ký bằng một vòng lặp theo
    kind). Bước nhận trường nó không dùng (NCC, chứng từ) bị từ chối, không lặng lẽ bỏ qua.
12. **`case_documents`:** bỏ NOT NULL của `po_case_id`, thêm `product_dev_case_id` với FK
    ghép CASCADE, CHECK `ck_case_documents_one_case` (`num_nonnulls = 1`), UNIQUE riêng phần
    phiên bản `uq_case_documents_tenant_id_product_case_doc_type_version`, UNIQUE
    `(tenant_id, workspace_id, product_dev_case_id, id)` (đích FK của vòng và phiếu, cũng
    là index phía FK), CHECK khóa đối tượng phủ `/product/`. Quét mồ côi và offboarding đi
    theo tiền tố: đã kiểm (`test_every_case_kinds_keys_are_swept_and_kept_alike`,
    `test_the_orphan_sweep_finds_a_product_documents_key`, offboarding ở điểm 1). **Đổi
    hình API của D:** `CaseDocumentView.po_case_id` thay bằng `case_kind` và `case_id`
    (route của D chưa phát hành; client và thẻ chứng từ cập nhật cùng lúc).
13. **Audit:** `propose` và mọi bước ghi `AuditEvent` (`supply_chain.product_case.<action>`)
    trong cùng giao dịch với trạng thái. Chứng minh bằng hai test (review vòng 1; test cũ
    `test_a_refused_step_leaves_no_audit_and_no_history` chỉ chạm guard version, trước
    mọi lần ghi, nên không phân biệt được):
    `test_a_step_refused_after_its_history_row_rolls_back_state_history_and_audit` (từ
    chối ở bước đóng vòng, sau UPDATE hồ sơ và INSERT lịch sử: không còn trạng thái, lịch
    sử hay audit) và `test_a_step_whose_audit_is_refused_leaves_no_state_and_no_history`
    (audit là lần ghi cuối và bị từ chối vì trùng id: trạng thái và lịch sử lùi theo).
    Không sửa audit thiếu của PO (P2).
14. **`propose` chỉ JSON**; ảnh SP tải lên sau qua route chứng từ (`product_image`); web tạo
    xong thì mở trang hồ sơ để tải ảnh.
15. **`pending_bod_review` tạm là điểm dừng:** không run approval nào; trang hiện nhãn
    glossary, một thông báo, và chỉ nút ngoại lệ, hủy (từ danh sách bước server trả). Ghi
    chú cho S2 đã thêm vào Comments của ticket 02.
16. **Web antd** `apps/web/app/supply-chain/product-cases/` (danh sách, chi tiết): trạng
    thái rỗng (chưa có, không khớp lọc), lỗi (câu của server, Thử lại), 403, 404, đang tải;
    lịch sử (Timeline), vòng mẫu (Table), chứng từ (thẻ của D, `caseKind="product"`), nút
    bước theo duty, khóa kèm lý do bằng chữ (Tooltip và dòng chữ bên dưới), form hỏi đúng
    NCC, lý do, chứng từ mà bước cần. Một bảng nhãn `PRODUCT_DEV_STATE_LABEL` theo
    CONTEXT.md (`components/supply-chain/product-case-labels.tsx`). Mục nav cạnh PO cases
    (scope `supply_chain.product_case.read`). **Thêm ngoài quyết định:** để trang không giữ
    bản sao TS của "bước nào cần lý do, NCC, chứng từ gì" và "duty nào", chi tiết hồ sơ trả
    `actions[]` (bước hợp lệ từ trạng thái, cái nó cần, `required_scope` theo policy của
    tenant), đọc từ cùng bảng domain và cùng hàm giải policy mà bước dùng. Nút đề xuất ở
    trang danh sách đọc `GET /product-action-duties`. **Sửa ở review vòng 1:** câu "trang
    không giữ bản sao" chỉ đúng cho bước, lý do, NCC, loại chứng từ và scope của từng bước.
    Trang còn giữ hai bản sao, có chủ ý, cả hai chỉ có thể thất bại theo hướng đóng:
    (a) bộ lọc chứng từ "của vòng hiện tại" trong `StepModal`
    (`uploaded_at >= opened_at`), chủ là `ProductDevelopmentCase._round_document`, server
    kiểm lại và trả 409; `Date.parse` giữ mili giây còn Postgres giữ micro giây, nên một
    chứng từ tải dưới 1 ms trước khi vòng mở được đưa ra rồi bị từ chối; (b) `dutyScope`
    dựng `supply_chain.duty.<duty>` cho nút đề xuất, chủ là `handlers.duty_scope`; đổi
    định dạng thì nút bị khóa với mọi người, server vẫn quyết. Mỗi bản sao ghi tên chủ
    của nó trong JSDoc và đổi cùng commit với chủ. Bỏ hẳn hai bản sao (server trả id chứng
    từ hợp lệ cho từng bước, và scope của `propose`) là việc sau nếu lead muốn: chi tiết
    hồ sơ khi đó phải đọc chứng từ, tức là qua thêm một scope. **Ghi chú đổi sau:** `@dw/ui`
    chưa có status tag và page header (antd-shell 07/08): dựng tại chỗ bằng antd `Tag`,
    `Typography`; đổi khi có. `newIdempotencyKey` của thẻ D tách ra
    `lib/idempotency-key.ts` (một chủ, cả hai nơi dùng).
17. **Migration:** `59e69efdfa37` (bảng và `case_documents`) rồi `7c422b849fe9` (vai), một
    head. Bốn tên ràng buộc rút `product_dev_case_id` thành `case` vì vượt 63 byte của
    Postgres (bị cắt im lặng). Câu RLS viết từng dòng (không vòng lặp) vì
    `verify_invariants.py` đọc chữ của migration và không thấy câu dựng trong vòng lặp
    (failure-modes #0: lần đầu nó đỏ trong khi `test_rls_coverage.py`, hỏi catalog, đã
    xanh). upgrade, downgrade về `f6a8142a6cd2`, upgrade lại chạy sạch trên DB nháp.

**Kiểm tra đã chạy (5/10/2026, kết quả thật):**

| Lệnh                                                                                                         | Kết quả                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `make ci` (lint, typecheck, test-unit, test-architecture, test-contract, eval-smoke, release-manifest-check) | xanh; unit 1762 passed, 3 skipped; contract 5 passed; eval smoke platform 4/4, supply_chain 22/22; manifest OK                |
| `make generate-contracts`                                                                                    | snapshot và `supply-chain.d.ts` tạo lại, CRLF đổi về LF; `make test-contract` 5 passed                                        |
| `make release-manifest`, rồi `make release-manifest-check`                                                   | manifest tạo lại (policy mới), OK                                                                                             |
| integration `dw_supply_chain` (có `.env`)                                                                    | 148 passed                                                                                                                    |
| integration `dw_platform` (toàn bộ)                                                                          | 200 passed                                                                                                                    |
| integration `apps/worker`                                                                                    | 1 passed                                                                                                                      |
| vitest `app/supply-chain/__tests__` và `components/supply-chain/__tests__/case-documents-card.test.tsx`      | 6 file, 37 passed (14 mới cho danh sách và chi tiết)                                                                          |
| `pnpm --filter @dw/web build`                                                                                | biên dịch xong, sinh 26 trang gồm `product-cases` và `product-cases/[id]`; chỉ lỗi EPERM symlink của bản standalone (Windows) |
| migration upgrade, downgrade, upgrade                                                                        | exit 0 cả ba                                                                                                                  |

**Mutation** (script có lượt đối chứng xanh trước; mỗi đột biến được kiểm là đã áp dụng,
chạy test, khôi phục, so byte):

| Gác bị bỏ                                             | Test                      | Kết quả | Khôi phục  |
| ----------------------------------------------------- | ------------------------- | ------- | ---------- |
| Kiểm biên bản ở `pass_sample`                         | unit domain, handler, API | đỏ      | byte giống |
| Luật vòng hiện tại (biên bản vòng 1 dùng cho vòng 2)  | unit, integration         | đỏ      | byte giống |
| Đóng dấu PIC từ người tạo                             | unit domain, handler, API | đỏ      | byte giống |
| `extra="forbid"` của body đề xuất                     | API                       | đỏ      | byte giống |
| Kiểm duty ở `AdvanceProductCase`                      | unit, API                 | đỏ      | byte giống |
| Mệnh đề workspace của policy RLS                      | integration               | đỏ      | byte giống |
| Trigger `closes_once`                                 | integration               | đỏ      | byte giống |
| `disabled` của nút bước bị khóa (web)                 | vitest                    | đỏ      | byte giống |
| DELETE của `dw_app` trên `product_dev_cases` (điểm 1) | integration offboarding   | đỏ      | byte giống |

**reviewing-feature-security:** §1 tenant (bốn bảng: tenant khác và workspace khác đọc
rỗng; dòng con ghi tên tenant, workspace và hồ sơ của chủ bị WITH CHECK của RLS từ chối
ở cả ba bảng con, vì FK ghép nhận dòng đó; UPDATE thô theo id của hồ sơ và vòng mẫu
không chạm dòng nào; dòng con ghi tên tenant của chính người gọi thì FK ghép từ chối;
khóa đối tượng có tenant và workspace; route trả 404); §2 quyền (duty kiểm ở handler; gọi thẳng handler và gọi route đều 403; PIC lấy từ
context); §5 audit cùng giao dịch, và biên bản đã đóng một vòng không xóa riêng được
(`test_a_document_a_round_was_closed_on_cannot_be_deleted_on_its_own`, FK); §6 override
hỏng thì từ chối thay vì rơi về mặc định. §3 không áp dụng: lát này không có tool hay run
agent nào (run BGĐ là của S2). §4 không áp dụng: không mô hình nào đọc gì; nội dung file chỉ
được dò chữ ký đầu file như ở D.

**Còn mở (không chặn):**

- Chưa mở trang trên trình duyệt thật (không chạy Playwright); review mã không thay cho
  xem trang (ui-quality, failure-modes #0).
- `lib/dates.ts` chưa định múi `Asia/Ho_Chi_Minh` và nhãn "giờ Việt Nam" (có từ trước).
- Luật thẻ-bảng ở khối điện thoại của `globals.css` vẫn áp lên bảng antd (có từ D; xóa thì
  hỏng các bảng shadcn còn lại).
- Seed demo chưa có người dùng `sc_rnd`.
- `dw_app` xóa được dòng `product_dev_cases` bằng SQL thô (như `po_cases`); không đường
  code nào làm.
- Đổi PIC (`reassign_pic`), danh sách Category (S6), luật ordering-vs-R&D (QE-16) nằm
  ngoài lát này.

### 2026-10-06 — review vòng 1: phân loại và sửa (implementer)

Mười bốn phát hiện, mỗi cái đã kiểm lại trên mã; không cái nào sai. Không commit.

| #   | Phát hiện                                                                          | Xử lý                                                                                                                                                                                                                                                                                                                                                                                                   |
| --- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `PUT /product-action-duties` không có test từ chối khi thiếu `action_duties.write` | Đúng. Thêm `test_setting_the_product_duties_needs_the_action_duties_write` (handler) và `test_setting_the_product_duties_without_the_write_is_403` (route: body hợp lệ, người gọi có `action_duties.read` và mọi duty; 403, không lưu gì).                                                                                                                                                              |
| 2   | Ba lệnh đọc khác không có test từ chối khi thiếu scope đọc                         | Đúng. `test_reading_needs_the_read_scope` tham số hóa qua `GetProductCase`, `ListProductCases`, `ListProductCaseTransitions`, `GetProductActionDuties` (người gọi có mọi duty, không scope đọc); route `test_every_read_refuses_a_caller_holding_only_duties_403` cho bốn `GET`. `GET /product-action-duties` gác bằng `action_duties.read`, không phải `product_case.read` (mọi vai `sc_*` có cả hai). |
| 3   | Không test nào đỏ nếu bỏ `duty.rnd` khỏi `sod_sc_rules_vs_operations`              | Đúng (cặp `(sc_process_admin, sc_rnd)` vẫn xung đột nhờ `duty.exceptions`, `document.write`). Test chữ ký cũ chỉ kiểm `document.write` thành `test_the_process_admin_cannot_also_hold_any_operation`: phía vận hành của luật phải chứa mọi scope trong `_OPERATIONS` (mọi `CaseDuty`, kể cả duty sau này).                                                                                              |
| 4   | Hủy hồ sơ khi mẫu đang test để vòng mở mãi                                         | Đúng. `cancel` đóng vòng mở với `rejected` (Hủy) trong cùng bước khi đang test hoặc tạm dừng lúc đang test; không thêm giá trị kết quả mới (giữ CHECK của quyết định 6). Test domain hai chiều, integration `test_cancelling_a_case_in_test_closes_its_round_in_the_same_save`. ADR 0016 sửa đổi điểm 5 và CONTEXT.md (Vòng mẫu) ghi lại.                                                               |
| 5   | `reason` gửi kèm bước không nhận lý do bị bỏ lặng lẽ                               | Đúng. `apply_product_action` từ chối lý do ở mọi bước ngoài `PRODUCT_REASON_REQUIRED_ACTIONS` (`DomainError`, 422). Test domain cho cả năm bước, route `test_a_reason_on_a_step_that_takes_none_is_refused_not_dropped`. Web vốn gửi `null` khi không có lý do.                                                                                                                                         |
| 6   | 409 từ database luôn nói `pass_sample`                                             | Đúng. Repository rút bước chờ ra trước giao dịch và dựng `document_refusal` từ hành động của bước đang ghi; bảng ràng buộc chỉ còn là tập tên. Integration `test_a_database_refusal_of_a_rejects_evaluation_names_reject_sample`.                                                                                                                                                                       |
| 7   | Fake `list_transitions`, `list_rounds` của test API trả `[]`                       | Đúng. Fake ghi dòng lịch sử, mở và đóng vòng (một lần) từ bước chờ, đọc lại giờ mở vòng hiện tại, và thu hẹp theo tenant và workspace. Test mới `test_the_history_and_rounds_are_the_cases_own` kiểm hình hai phản hồi.                                                                                                                                                                                 |
| 8   | Web giữ bản sao luật "chứng từ của vòng hiện tại" và định dạng scope duty          | Đúng là bản sao; câu "trang không giữ bản sao" sai. Chọn sửa lời khai (điểm 6 và 16 ở trên, JSDoc của `StepModal` và `dutyScope` ghi tên chủ), không bỏ bản sao: cả hai chỉ thất bại theo hướng đóng, server kiểm lại. Bỏ hẳn là việc sau, cần lead quyết.                                                                                                                                              |
| 9   | `sc_rnd` có `duty.exceptions`, ngoài danh sách của quyết định 8                    | Đúng. Ghi là lệch cần lead chấp nhận ở QO-2 (điểm 8 ở trên, ADR 0016 sửa đổi điểm 1): R&D cũng ngắt và tiếp tục được Hồ sơ PO. Mã không đổi.                                                                                                                                                                                                                                                            |
| 10  | Test âm RLS ghi không chứng minh gì cho ba bảng con và UPDATE                      | Đúng (dòng con ghi tenant của chính người gọi bị FK chặn, không phải RLS). Thêm `test_a_child_naming_the_owners_case_is_refused_by_rls` (ba bảng con × tenant khác, workspace khác; dòng ghi tên tenant, workspace, hồ sơ, vòng và chứng từ thật của chủ; FK nhận, WITH CHECK từ chối, số dòng không đổi) và `test_another_tenant_or_workspace_updates_no_case_or_round_row` (UPDATE thô theo id).      |
| 11  | Test audit cùng giao dịch không thấy audit chuyển sang giao dịch khác              | Đúng. Thêm hai test (điểm 13 ở trên): từ chối sau INSERT lịch sử, và audit bị từ chối là lần ghi cuối.                                                                                                                                                                                                                                                                                                  |
| 12  | Như 8, nhìn từ phía test                                                           | Như 8.                                                                                                                                                                                                                                                                                                                                                                                                  |
| 13  | Không thêm scope tạo/ghi là lệch quyết định 9                                      | Đúng. Ghi là lệch cần lead chấp nhận ở QO-2 (điểm 9 ở trên, ADR 0016 sửa đổi điểm 1). Mã không đổi.                                                                                                                                                                                                                                                                                                     |
| 14  | Vitest chi tiết thiếu trạng thái lỗi tải                                           | Đúng. Thêm test: 500 hiện câu của server, "Thử lại" gọi lại và trang hiện hồ sơ.                                                                                                                                                                                                                                                                                                                        |

**Mutation vòng 1** (script có lượt đối chứng xanh trước; mỗi đột biến được kiểm là đã
áp dụng, chạy test với `-x`, khôi phục, so byte; cả 16 đỏ đúng ở test nhắm tới, không
lỗi alembic hay thu thập):

| Gác bị bỏ                                                       | Test                                       | Kết quả | Khôi phục  |
| --------------------------------------------------------------- | ------------------------------------------ | ------- | ---------- |
| `authz.require` của `SetProductActionDutiesOverride`            | unit handler, API                          | đỏ      | byte giống |
| `authz.require` của `GetProductActionDuties`                    | unit handler, API                          | đỏ      | byte giống |
| `authz.require` của `ListProductCases`                          | unit handler, API                          | đỏ      | byte giống |
| `authz.require` của `ListProductCaseTransitions`                | unit handler, API                          | đỏ      | byte giống |
| Từ chối lý do ở bước không nhận lý do                           | unit domain, API                           | đỏ      | byte giống |
| `cancel` đóng vòng mở                                           | unit domain, integration                   | đỏ      | byte giống |
| 409 nêu bước đang ghi (thay bằng `pass_sample` cố định)         | integration                                | đỏ      | byte giống |
| WITH CHECK của bốn policy thành `(true)`                        | integration, tenant khác và workspace khác | đỏ      | byte giống |
| WITH CHECK chỉ còn tenant                                       | integration, workspace khác                | đỏ      | byte giống |
| USING thành `(true)`                                            | integration UPDATE, tenant khác            | đỏ      | byte giống |
| USING chỉ còn tenant                                            | integration UPDATE, workspace khác         | đỏ      | byte giống |
| Audit ghi ở phiên riêng TRƯỚC trạng thái                        | integration                                | đỏ      | byte giống |
| Audit ghi ở phiên riêng SAU commit                              | integration                                | đỏ      | byte giống |
| Fake API trả `[]` cho lịch sử và vòng (fake cũ)                 | API                                        | đỏ      | byte giống |
| Bỏ `duty.rnd` khỏi `sod_sc_rules_vs_operations` (migration vai) | integration vai                            | đỏ      | byte giống |
| "Thử lại" của trang chi tiết không tải lại (web)                | vitest                                     | đỏ      | byte giống |

Với `-x`, đột biến RLS chỉ được thấy đỏ ở tham số đầu (`product_dev_case_state_transitions`);
đột biến áp cho cả bốn policy cùng lúc.

**Kiểm tra vòng 1 (6/10/2026, kết quả thật):**

| Lệnh                                                                  | Kết quả                                                                                                                                                                       |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make ci`                                                             | xanh (lần đầu đỏ ở `ruff format` hai file test; đã format); unit 1782 passed, 3 skipped; contract 5 passed; eval smoke 4/4, 22/22; manifest OK `sha256:af070fd3…` (không đổi) |
| integration `dw_supply_chain` (có `.env`)                             | 160 passed                                                                                                                                                                    |
| integration `dw_platform`                                             | 200 passed                                                                                                                                                                    |
| integration `apps/worker`                                             | 1 passed                                                                                                                                                                      |
| vitest `app/supply-chain/__tests__` và `case-documents-card.test.tsx` | 6 file, 38 passed                                                                                                                                                             |
| `pnpm --filter @dw/web typecheck`, `lint`                             | xanh; lint chỉ còn cảnh báo có từ trước ở `components/assistant-ui`                                                                                                           |
| `prettier --check` file markdown và web đã sửa                        | xanh                                                                                                                                                                          |

Không đổi route hay model nên OpenAPI, client sinh và release manifest không đổi. Không
chạy lại upgrade/downgrade migration: vòng này không sửa migration (đột biến migration
chạy trên database thử tạo lại mỗi phiên và đã khôi phục byte).

### 2026-10-06 — review vòng 2: phân loại và sửa (implementer)

Ba phát hiện, mỗi cái đã kiểm lại trên mã; cả ba đúng. Không commit.

| #   | Phát hiện                                                                                                                                  | Xử lý                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Hai lớp phòng thủ sau RLS không có test nào đỏ khi bỏ: so workspace ở `_case_in_workspace` (handler) và `_in_scope` ở `get()` (repository) | Đúng: fake của test unit và API đã thu hẹp sẵn, integration thì RLS chặn trước. Giữ cả hai lớp (D cũng có lớp repository, và có test riêng cho nó), thêm test chạm từng lớp: `test_the_handlers_refuse_another_workspaces_case_a_repository_let_through` (fake `LeakyCases` có `get` không thu hẹp; `GetProductCase`, `ListProductCaseTransitions`, `AdvanceProductCase` đều 404, hồ sơ không đổi) và `test_the_repository_filters_by_tenant_and_workspace_even_where_rls_does_not_apply` (tenant khác, workspace khác; repository chạy trên kết nối migrator, bỏ qua RLS: `get`, `case_workspace`, `list_page`, `list_transitions`, `list_rounds` rỗng, `save` bị từ chối, chủ vẫn đọc được).                                                    |
| 2   | Quyết định 9 (bắt buộc) chưa làm: mẫu PO có `po_case.write` gác `CreatePOCase`, còn `propose` chỉ gác bằng duty                            | Đúng; làm đúng chữ quyết định. `PRODUCT_CASE_WRITE = "supply_chain.product_case.write"` ở `handlers.py`; `ProposeProductCase` hỏi nó trước, rồi duty của `propose` (điểm 7; giữ để mục `propose` của policy vẫn có người đọc). Migration `7c422b849fe9` cấp scope cho đúng các vai đang có `po_case.write` (đọc từ dòng; hôm nay `sc_operator`) và thêm nó vào phía vận hành của `sod_sc_rules_vs_operations`; downgrade gỡ cả hai. Nút đề xuất ở web khóa kèm lý do khi thiếu scope này (bản sao chuỗi scope ghi tên chủ, thất bại theo hướng đóng). Hệ quả: công ty ghi đè `propose` sang duty khác thì người làm vẫn cần `product_case.write`.                                                                                                 |
| 3   | Quyết định 8 (bắt buộc) bị lệch: `sc_rnd` có `duty.exceptions`, nên R&D ngắt và tiếp tục được Hồ sơ PO (`AdvancePOCase` chỉ hỏi duty)      | Đúng; làm đúng chữ quyết định. `sc_rnd` = `sc_viewer` + `duty.rnd` + `document.write`. `test_everyone_running_cases_can_raise_and_clear_an_exception` nay chạy trên `_PO_OPERATING_ROLES` (R&D không làm bước PO nào); thêm `test_rnd_holds_exactly_the_viewer_its_duty_and_the_document_write`. Hệ quả, theo quyết định 7 (bước ngoại lệ soi gương PO, `exceptions`): với policy nền, R&D không chờ bên ngoài, báo chặn, cần xem xét hay tiếp tục được hồ sơ phát triển; các vai vận hành PO làm. Công ty muốn R&D làm thì ghi đè các bước đó sang `rnd` trong `supply_chain_product_action_duties` (không chạm PO). Từ chối thừa thì sửa được, cho R&D quyền trên PO thì không (fail closed). Cần Đạt xác nhận ở QO-2 cùng các quyết định khác. |

Đổi kèm: ADR 0016 sửa đổi điểm 1 viết lại (không còn hai điểm lệch); điểm 8, 9 của mục
5/10 ở trên viết lại; comment của
`configs/policies/supply_chain_product_action_duties@1.0.0.yaml` (ai làm bước ngoại lệ,
đề xuất cần scope ghi), nên release manifest tạo lại `sha256:0554b039…` (history mới
`manifest-0554b0394bc3.json`; `manifest-af070fd3bf95.json` của vòng trước chưa từng
commit, vẫn để đó, lead quyết giữ hay bỏ). OpenAPI và client sinh không đổi (không đổi
route hay model). Database dev (compose) đã chạy bản cũ của `7c422b849fe9`: cần
`downgrade 59e69efdfa37` rồi `upgrade head` để vai khớp.

**Mutation vòng 2** (lượt đối chứng xanh trước; mỗi đột biến kiểm là đã áp dụng, chạy
test, khôi phục, so sha256):

| Gác bị bỏ                                                              | Test                                          | Kết quả | Khôi phục  |
| ---------------------------------------------------------------------- | --------------------------------------------- | ------- | ---------- |
| So workspace ở `_case_in_workspace`                                    | unit (fake `LeakyCases`)                      | đỏ      | byte giống |
| `_in_scope` ở `get()`                                                  | integration, kết nối migrator, cả hai tham số | đỏ      | byte giống |
| `_in_scope` ở `case_workspace()`                                       | như trên                                      | đỏ      | byte giống |
| `_in_scope` ở `list_page()`                                            | như trên                                      | đỏ      | byte giống |
| `_in_scope` ở `list_transitions()`                                     | như trên                                      | đỏ      | byte giống |
| `_in_scope` ở `list_rounds()`                                          | như trên                                      | đỏ      | byte giống |
| `_in_scope` ở `save()` (đỏ vì FK của dòng lịch sử, sau khi UPDATE lọt) | như trên                                      | đỏ      | byte giống |
| `authz.require(PRODUCT_CASE_WRITE)` ở `ProposeProductCase`             | unit handler, API                             | đỏ      | byte giống |
| Migration: `sc_rnd` lại có `duty.exceptions`                           | integration vai                               | đỏ      | byte giống |
| Migration: không cấp `product_case.write` cho ai                       | integration vai                               | đỏ      | byte giống |
| Migration: cấp `product_case.write` cho mọi vai trừ quản trị           | integration vai                               | đỏ      | byte giống |
| Migration: `product_case.write` không vào `sod_sc_rules_vs_operations` | integration vai                               | đỏ      | byte giống |
| Web: nút đề xuất bỏ qua `product_case.write`                           | vitest                                        | đỏ      | byte giống |

**reviewing-feature-security §2** (quyền) cho phần đổi: scope ghi kiểm ở handler, nơi ghi
xảy ra (handler và route đều có test 403); nút web chỉ là trình bày. §1 (tenant): hai lớp
phòng thủ sau RLS nay có test riêng.

**Kiểm tra vòng 2 (6/10/2026, kết quả thật):**

| Lệnh                                                                         | Kết quả                                                                                                                                                                                                  |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make ci`                                                                    | EXIT 0; ruff, format (634 file), mypy 465 file sạch; unit 1786 passed, 3 skipped; import-linter 9 kept; contract 5 passed; eval smoke 4/4, 22/22; manifest OK `sha256:0554b039…`; "local CI gate passed" |
| integration `dw_supply_chain` (có `.env`)                                    | 164 passed                                                                                                                                                                                               |
| integration `dw_platform`                                                    | 200 passed                                                                                                                                                                                               |
| integration `apps/worker`, `apps/api`                                        | 1 passed                                                                                                                                                                                                 |
| vitest `app/supply-chain/__tests__`, `components/supply-chain/__tests__`     | 6 file, 39 passed                                                                                                                                                                                        |
| migration trên database nháp: upgrade head, downgrade hai bước, upgrade head | exit 0 cả bốn; một head `7c422b849fe9`; sau downgrade về `59e69efdfa37` không vai nào còn scope `product_case.*`, luật không còn `product_case.write`, không còn `sc_rnd`                                |
| `git ls-files --eol` manifest, ref                                           | LF (đã đổi CRLF do `release_manifest.py` ghi về LF)                                                                                                                                                      |

- 2026-10-06, lead: verifier vòng 2 còn một mutation sống (U16: bỏ lệnh từ chối id chứng từ không đọc được trong `AdvanceProductCase`, test vẫn xanh). Đã thêm `test_reject_naming_a_document_the_caller_cannot_read_is_refused` và `test_a_step_naming_a_document_the_caller_cannot_read_is_refused`; bỏ lệnh từ chối thì cả hai đỏ, khôi phục thì xanh. Review nêu route `PUT /product-action-duties` không có test âm: thêm `test_product_action_duties_handlers_refuse_without_their_scopes` (test_handlers.py); bỏ `require` của Set thì đỏ. Chấp nhận lệch quyết định 1: `dw_app` giữ DELETE trên `product_dev_cases` như `po_cases`, vì purge offboarding chỉ xóa bảng `dw_app` được DELETE (đo: thiếu thì 2 hồ sơ sót). Bỏ `manifest-af070fd3bf95.json` (bản trung gian vòng 1, chưa từng commit). Sau đó: lint, typecheck, unit 1789, architecture, release-manifest-check xanh.
