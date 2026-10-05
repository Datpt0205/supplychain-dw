# 01 — `supply_chain.case_documents`, `CaseDocumentStoragePort`, route tải lên và tải xuống

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

Chứng từ gắn vào hồ sơ, cách ly theo tenant và workspace ở cả database lẫn object
storage (spec, Mục tiêu).

## Việc cần làm

1. **Migration** (id hex ngẫu nhiên): `supply_chain.case_documents` theo ADR 0021, với
   `po_case_id` FK ngay; `product_dev_case_id` thêm ở `stage-1/issues/01` khi bảng
   `product_dev_cases` tồn tại, cùng CHECK "đúng một FK khác NULL". RLS FORCE hình
   workspace chuẩn; index cho danh sách theo hồ sơ bắt đầu bằng `tenant_id`; grant cho
   `dw_app` (không DELETE); `doc_type` CHECK theo danh sách của ADR; UNIQUE riêng phần
   `(tenant_id, po_case_id, doc_type, version) WHERE po_case_id IS NOT NULL` (bản cho
   `product_dev_case_id` thêm cùng cột đó ở S1); FK `(tenant_id, po_case_id)` tới
   `po_cases (tenant_id, id)` như `bc3f0c1279fd`.
2. **Port** `CaseDocumentStoragePort` trong `dw_supply_chain/application/ports.py`;
   adapter trên client S3 sẵn có; chỉ composition root import adapter.
3. **Handler:** `UploadCaseDocument` (scope `supply_chain.document.write`; dựng
   `object_key` ở server; tính `sha256`; trần kích thước từ settings; `content_type`
   cho phép: PDF, JPEG, PNG, XLSX, DOCX, EML, MSG; `version` tăng theo
   `(case, doc_type)`, vi phạm UNIQUE đổi thành 409 theo tên ràng buộc; ghi đối tượng
   rồi chèn dòng, chèn thất bại thì xóa đối tượng vừa ghi; audit), `ListCaseDocuments`, `DownloadCaseDocument`
   (`supply_chain.document.read`, đọc dòng dưới RLS rồi stream đối tượng).
4. **Route:** `POST`/`GET /po-cases/{id}/documents`, `GET /documents/{id}/content`, có
   `Idempotency-Key` cho POST.
5. **Vai:** thêm hai scope vào các vai `sc_*` phù hợp bằng migration `platform.roles`;
   `test_role_catalogue.py` cập nhật.
6. **Offboarding:** xuất và xóa đối tượng dưới `supply_chain/{tenant_id}/`.
7. **Quét mồ côi:** một `RetentionPrunePort` ở lane `retention` xóa khóa dưới
   `supply_chain/` không có dòng và cũ hơn một ngày.
8. **Web:** khối "Chứng từ" trên trang Hồ sơ PO, bằng antd (danh sách, tải lên, tải
   xuống), không sửa phần khác của trang.

## Tiêu chí chấp nhận

- [x] **Test âm RLS:** tenant B không đọc dòng của A; workspace khác cùng tenant không
      đọc; `test_rls_coverage.py` xanh.
- [x] **Test âm route:** tải lên vào hồ sơ của tenant khác trả 404 và không ghi đối
      tượng nào; tải xuống id của tenant khác trả 404; workspace khác trả 404.
- [x] `object_key` luôn bắt đầu bằng `supply_chain/{tenant}/{workspace}/` của context
      truy cập; tên file chứa `../` không đổi khóa.
- [x] File quá trần 413; `content_type` ngoài danh sách 415; hai lần POST cùng khóa
      idempotency tạo một phiên bản.
- [x] Offboarding: integration test xóa đối tượng của tenant A, giữ của tenant B.
- [x] Hai lần tải lên đồng thời cùng `(hồ sơ, doc_type)` (hai giao dịch thật): hai
      phiên bản khác nhau hoặc một 409, không bao giờ hai dòng cùng phiên bản. Mutation:
      bỏ UNIQUE thì test đỏ.
- [x] Chèn dòng thất bại (lỗi tiêm ở repository giả): đối tượng vừa ghi bị xóa. Lượt quét
      xóa khóa không có dòng cũ hơn một ngày, giữ khóa có dòng và khóa mới.
- [x] Mutation: bỏ tiền tố tenant trong `object_key` thì test đỏ (ghi vào Comments).
- [x] `make ci` xanh; integration của `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 ("no documents table and no object storage per case"), mục 3
  (`CaseDocument`), mục 2 hàng 11, 14, 16.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "New tables"; câu "The platform already has ...
  `FeedbackAttachmentStoragePort` (`dw_platform/application/ports.py:212,235`)".
- `docs/products/elmich/process.md` mục 2.

## Comments

### 2026-10-05 — lát D đã làm (implementer); quyết định tạm của lead, chờ Đạt duyệt ở QO-2

**Trạng thái:** mọi tiêu chí chấp nhận đạt, trừ `make ci` xanh: bước `lint` của nó đỏ vì
`prettier --check` báo hai file của lát A
(`.claude/plans/supply-chain/approval-decider-scope/issues/01-required-scope.md` và
`.../approval-decider-scope/spec.md`), không đổi so với HEAD `dabf5c4`. Lát này không sửa
hai file đó (ngoài phạm vi); cách sửa là `prettier --write` hai file ấy. Vì vậy Status để
`ready-for-agent`. Mọi bước còn lại của `make ci` chạy riêng và xanh (bảng dưới).
**Vòng review 2:** hai file đó đã được `prettier --write` (chỉ khoảng trắng), `make ci`
xanh trọn vẹn; xem mục vòng 2 cuối Comments. Status do lead đổi.

**Quyết định tạm (bắt buộc cho lát này), mỗi cái đã làm** (ADR 0021 có đoạn "Sửa đổi
2026-10-05"):

1. **FK và offboarding.** FK `(tenant_id, workspace_id, po_case_id)` tới
   `po_cases (tenant_id, workspace_id, id)` `ON DELETE CASCADE`, có index
   `ix_case_documents_tenant_id_workspace_id_po_case_id`. `dw_app` chỉ SELECT, INSERT
   (thu UPDATE, DELETE, TRUNCATE). CASCADE là đường duy nhất một dòng rời bảng; purge của
   offboarding xóa `po_cases` rồi cascade. Test:
   `test_case_documents.py::test_offboarding_exports_every_workspace_and_purges_only_that_tenant`
   (tenant có chứng từ ở hai workspace; tenant khác giữ nguyên).
   **QE-02, câu hỏi thêm cho Elmich (lead đưa vào area file):** thời hạn lưu theo luật
   của chứng từ `payment_docs` và `deposit_docs`; hôm nay chúng rời hệ thống khi hồ sơ PO
   bị xóa (chỉ offboarding xóa hồ sơ).
2. **Workspace.** RLS ENABLE + FORCE, policy `tenant_isolation_case_documents` đúng dạng
   `tenant AND (workspace OR app.workspace_scope = 'tenant')` ở cả USING và WITH CHECK.
   UNIQUE mới `uq_po_cases_tenant_id_workspace_id_id`. Handler tải lên và liệt kê trả 404
   khi workspace của hồ sơ khác của người gọi, trước khi ghi đối tượng. RLS của `po_cases`
   không đổi. Đã sửa docstring "no table is narrowed by workspace yet" ở
   `test_rls_coverage.py::test_the_scope_rules_catch_the_wrong_shapes`; thêm
   `test_the_policy_is_the_one_workspace_shape_on_both_sides` để luật dạng policy không
   qua một cách rỗng.
3. `po_case_id NOT NULL`; UNIQUE `(tenant_id, workspace_id, id)` trên `case_documents` cho
   FK ghép của S1 (các bảng S1 thu hẹp theo workspace).
4. **Bucket** `case-documents`, setting `case_documents_bucket` ở API và worker (biến chung
   `CASE_DOCUMENTS_BUCKET`, dòng chú thích trong `.env.example`). Adapter
   `MinioCaseDocumentStorage` (`dw_supply_chain/adapters/storage/`) trên client S3 của
   composition root, tự tạo bucket khi ghi. Khóa dựng ở server
   `supply_chain/{tenant}/{workspace}/po/{case}/{document}` (`ObjectKey`, một chủ); CHECK
   `ck_case_documents_object_key` buộc khóa khớp id của dòng.
5. **Router riêng** `presentation/document_routes.py`, mount trong `main.py` bằng guard
   riêng (ba handler chứng từ); không thêm vào chuỗi all-or-nothing. Test
   `test_the_documents_router_mounts_on_its_own_guard`. **Tiền đề của quyết định không
   đúng** (vòng review 1): không có S3 thì `build_container` trả về trước khi wire bất
   kỳ handler Supply Chain nào, vì Supply Chain dựng từ `container.runtime`, mà runtime
   cần object storage. Một host không có bucket không còn API Supply Chain nào, có hay
   không có router riêng. Router riêng giữ lại (vô hại); guard `if minio is not None`
   thừa trong `wiring.py` và test rỗng đi kèm đã bỏ. Lead cần biết điều này.
6. **Offboarding worker** liệt kê, xuất (`blobs/case_documents/...`) và xóa
   `supply_chain/{tenant_id}/` trong bucket chứng từ. Unit với fake: hai bucket, tenant A bị
   xóa, tenant B giữ nguyên, và khóa `…/{A}x/…` (chỉ tiền tố thiếu `/` mới lấy) cũng
   giữ nguyên. Integration với S3 thật:
   `apps/worker/tests/integration/test_offboarding_buckets.py`.
7. **Quét mồ côi:** lane `supply_chain_document_orphans` (đăng ký như
   `notifications_retention`, nhịp 3600 s, có tên trong `test_worker.py`), cài
   `RetentionPrunePort` bằng `SweepOrphanDocuments`: trang 500 khóa có con trỏ; khóa cũ hơn
   1 ngày; đọc tenant và workspace từ khóa, hỏi trong phiên tenant thường. `list_after` của
   adapter trả `last_modified` (minio `list_objects`). Unit: cũ không dòng bị xóa; có dòng
   giữ; mới giữ; khóa sai dạng bỏ qua và ghi log; hỏi đúng scope của khóa; một tenant lỗi
   không chặn tenant khác; con trỏ đi tiếp rồi quay vòng. Integration (S3 và Postgres
   thật): `test_case_document_storage.py::test_the_sweep_deletes_only_old_keys_no_row_holds`.
8. `ErrorCode.PAYLOAD_TOO_LARGE` → 413, `UNSUPPORTED_MEDIA_TYPE` → 415 (kernel, bảng status
   của API, `ErrorCode` TS). Contract `apps/api/tests/contract/test_error_codes.py`: mọi mã
   có status riêng, bản TS khớp bản Python.
9. Trần `case_document_max_bytes` (mặc định 25 MiB) tiêm vào `UploadCaseDocument`; route
   đọc tối đa `max_bytes + 1` byte vào bộ nhớ. Route tải lên dùng route class
   `body_capped_route`: `Content-Length` vượt trần + 64 KiB, hoặc thân không khai độ dài,
   → 413 từ header, trước khi parse form và trước xác thực (vòng review 1). Bảy loại; quyết bằng loại khai báo và chữ ký đầu file
   (PDF, PNG, JPEG, ZIP, OLE), EML theo loại khai báo; lệch → 415. Test wiring: setting
   được đọc (`test_case_documents_are_wired_from_their_settings`).
10. Tải xuống đọc cả file (dưới trần), port không có stream. `attachment` với `filename*`
    RFC 5987 và tên ASCII dự phòng (bỏ ký tự điều khiển, dấu nháy, `\`),
    `X-Content-Type-Options: nosniff`, `Content-Type` là loại đã lưu.
11. **Idempotency multipart:** `get_form_idempotent_operation` không đọc body; route gọi
    `claim_fields({case_id, doc_type, filename, content_type, sha256})` sau khi parse form.
    Đã sửa chú thích sai ở `get_idempotent_operation`. Thêm: operation chưa claim không bao
    giờ release hay ghi vào khóa của request khác. Test: hai POST cùng khóa, boundary khác
    nhau → một phiên bản, lần hai phát lại đúng phản hồi đầu (201); cùng khóa, byte khác →
    409 `idempotency_conflict`.
12. `version = max+1` trong INSERT; UNIQUE riêng phần
    `uq_case_documents_tenant_id_po_case_id_doc_type_version` là chốt; adapter đổi vi phạm
    thành `ConflictError` theo tên ràng buộc. Test hai giao dịch thật (khóa EXCLUSIVE giữ hai
    INSERT rồi thả cùng lúc, 5 vòng).
13. Ghi đối tượng trước, chèn dòng sau; chèn lỗi thì xóa đối tượng (best-effort, có log).
14. Audit `supply_chain.document.upload` trong cùng giao dịch (`SqlAuditRepository`). Tải
    xuống không audit (không bản ghi cùng loại nào audit khi đọc).
15. Scope `DOCUMENT_READ`/`DOCUMENT_WRITE` trong `handlers.py`; read cho 7 vai, write cho 5
    vai vận hành; write vào phía vận hành của `sod_sc_rules_vs_operations`; cùng migration.
16. `DocumentType` (StrEnum, 14 giá trị) là chủ; test so CHECK với enum (và CHECK
    `content_type` với `ALLOWED_CONTENT_TYPES`).
17. **Web:** `components/supply-chain/case-documents-card.tsx` (antd Card, Table, Select,
    Upload; tải xuống qua API client dạng blob; trạng thái đang tải, lỗi kèm "Thử lại",
    rỗng; `DOC_TYPE_LABEL` một bảng nhãn), `caseKind` là prop (`"po"`), thêm vào cuối trang
    Hồ sơ PO, không sửa phần khác. Thiếu `supply_chain.document.write`: chọn loại, chọn file
    và Tải lên đều khóa, lý do hiện bằng chữ và trong Tooltip. Khóa idempotency tạo một lần
    cho mỗi (loại, file), dùng lại khi thử lại. Client: `listCaseDocuments`,
    `uploadCaseDocument`, `downloadCaseDocument`; zod `caseDocumentSchema` khớp
    `CaseDocumentView` (SameType).
18. Migration `f6a8142a6cd2` (hex của alembic), `down_revision = 5d3965984679`, một head.
    Downgrade −1 rồi upgrade head trên DB thử: bảng 1→0→1, UNIQUE trên `po_cases` 1→0→1,
    vai có read 7→0→7, có write 5→0→5, scope trong luật SoD True→False→True.

**Lệch khỏi ticket/quyết định:**

- `minio` thêm vào phụ thuộc của `dw_supply_chain`, vì adapter nằm ở
  `dw_supply_chain/adapters/storage/` và dùng chung cho API và worker (worker không được phụ
  thuộc `dw_api`). Không hợp đồng import-linter nào cấm.
- Compose `s3-setup` tạo sẵn bucket `case-documents`: liệt kê một bucket chưa tồn tại làm
  offboarding thất bại (đo được: integration test đỏ khi bucket chưa có). Bucket `feedback`
  có cùng lỗi tiềm ẩn từ trước (không tạo ở `s3-setup`); không sửa ở đây.
- `DownloadCaseDocument` không tự so workspace lần nữa: repository (RLS cùng bộ lọc riêng)
  là chủ của việc đó; bản so lần hai không làm test nào đỏ được (M7 vòng 1) nên đã bỏ, và
  thêm test bộ lọc riêng của repository trên kết nối bỏ qua RLS.
- POST trả 201; tải xuống thêm `Cache-Control: private, no-store`.
- `list_for_case` không nhận `case_kind` (chỉ có PO); S1 thêm khi cần.
- Quét mồ côi dùng lại `sweep_context` (principal hệ thống của sweep follow-up).

**Còn mở (không chặn lát):**

- Route phản hồi feedback (`routes/v1/feedback.py`, có từ trước) cùng dạng lỗ mà vòng
  review 1 đã bịt cho chứng từ: form được parse (file ghi ra đĩa, không giới hạn) trước
  xác thực. Không sửa ở lát này; `body_capped_route` dùng lại được ở đó.
- Hosting (nginx trên server, cấu hình không nằm trong repo): `client_max_body_size` mặc
  định của nginx là 1 MB, nhỏ hơn trần 25 MiB, nên phải đặt ít nhất trần + 64 KiB cho
  `/api/v1/supply-chain/po-cases/*/documents`; và giữ `proxy_request_buffering on` (mặc
  định) để API nhận `Content-Length` chứ không phải thân chunked, vốn bị route trả 413.
  Chưa đo trên server.
- `formatDateTime` dùng giờ của trình duyệt, chưa ghi "giờ Việt Nam" (formatter có sẵn).
- Card chưa hiện tên người tải lên (API trả `uploaded_by` là id); để ticket W.
- `next build` không chạy trong lát này.

**Đi qua `reviewing-feature-security`:**

- Tenant: bảng mới RLS FORCE hai chiều; phiên tenant đặt theo từng giao dịch; khóa đối
  tượng có tenant và workspace. Test âm: tenant B và workspace khác đọc được 0 dòng
  (repository, SQL thô, kết nối không scope); tải lên, liệt kê, tải xuống của tenant hoặc
  workspace khác → 404 và không ghi đối tượng nào.
- Phân quyền: kiểm ở handler, nơi ghi và đọc; danh tính từ `AccessContext`. Test HTTP trực
  tiếp: thiếu write → 403, không ghi gì. Nút bị khóa trên web chỉ là phần hiển thị.
- Autonomy/approval: không thêm tool hay tầm với nào cho agent (không áp dụng).
- Nội dung không tin cậy: nội dung file không vào mô hình; kiểm chữ ký đầu file; luôn
  `attachment` và `nosniff`; tên file chỉ là nhãn, không vào khóa, không phá được header
  (`test_document_routes.py`). Không có bề mặt mô hình mới nên không thêm eval
  prompt-injection.
- Audit: tải lên ghi audit trong cùng giao dịch. FK là CASCADE theo quyết định 1 (khác lời
  khuyên RESTRICT của checklist cho provenance; lý do ở ADR).
- Vòng đời: đối tượng tạo khi tải lên; mất khi chèn lỗi, khi quét mồ côi, khi offboarding.
  Kiểm quyền, hồ sơ, kích thước, loại trước khi ghi. DB lỗi trong lượt quét → không xóa gì
  của tenant đó.
- Lockfile đổi một cạnh phụ thuộc (`minio` đã có trong lock); compose không đổi image nào.

**Lệnh đã chạy (5/10/2026, Windows, Git Bash):**

| Lệnh                                                                                   | Kết quả                                                                                                                                        |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| `make lint`                                                                            | **đỏ**: ruff check/format xanh (623 file); `prettier --check` đỏ ở hai file lát A nói trên (không đổi từ HEAD); eslint web chỉ cảnh báo có sẵn |
| `make typecheck`                                                                       | xanh: mypy 456 file; tsc 4 gói                                                                                                                 |
| `make test-unit`                                                                       | xanh: 1567 passed, 3 skipped                                                                                                                   |
| `make test-architecture`                                                               | xanh: import-linter 9 kept, 0 broken; declared-dependency 12 gói; invariants ok                                                                |
| `make test-contract`                                                                   | xanh: 5 passed (snapshot 2, mã lỗi 3)                                                                                                          |
| `make generate-contracts`                                                              | đã chạy; `openapi.json` đổi CRLF về LF; `supply-chain.d.ts` thêm 3 route, `CaseDocumentView`, `DocumentType`                                   |
| `make release-manifest-check`                                                          | xanh (không thêm config có phiên bản)                                                                                                          |
| `make eval-smoke`                                                                      | xanh: platform 4/4, supply_chain 22/22                                                                                                         |
| `pytest -m integration packages/python/dw_supply_chain/tests apps/worker/tests` (.env) | xanh: 117                                                                                                                                      |
| `pytest -m integration packages/python/dw_platform/tests` (.env)                       | xanh: 199                                                                                                                                      |
| `pnpm --filter @dw/web exec vitest run`                                                | xanh: 19 file, 102 test (trong đó `case-documents-card.test.tsx`: 9)                                                                           |

**Mutation (control xanh trước; mỗi ca: sửa đúng một chỗ có kiểm, chạy, khôi phục, so byte
khớp bản gốc; `git diff --stat` trước và sau không đổi):**

| #   | Bỏ guard                                           | Test đỏ                                                                                                                                                                                                                                                                                                                                                                                                |
| --- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| M1  | UNIQUE `version` thành INDEX thường                | `test_two_concurrent_uploads_never_share_a_version` (10 dòng, 5 phiên bản)                                                                                                                                                                                                                                                                                                                             |
| M2  | bỏ tiền tố tenant trong `ObjectKey.value`          | `test_case_document.py::test_the_key_is_built_from_the_verified_ids_only`                                                                                                                                                                                                                                                                                                                              |
| M3  | bỏ so workspace của hồ sơ                          | `test_uploading_to_another_workspaces_case_is_not_found_and_writes_nothing`                                                                                                                                                                                                                                                                                                                            |
| M4  | chèn lỗi không xóa đối tượng                       | `test_a_failed_insert_removes_the_object_it_just_wrote`                                                                                                                                                                                                                                                                                                                                                |
| M5  | bỏ kiểm trần                                       | `test_a_file_over_the_cap_is_413_and_one_at_the_cap_is_taken`                                                                                                                                                                                                                                                                                                                                          |
| M6  | bỏ kiểm chữ ký đầu file                            | `test_a_type_outside_the_list_or_against_its_bytes_is_415[application/pdf-<html><script>]`                                                                                                                                                                                                                                                                                                             |
| M7  | bỏ bộ lọc workspace riêng của repository (RLS còn) | vòng 1 (bản so lần hai ở handler) **xanh**, đã bỏ bản đó; vòng 2 đỏ: `test_the_repository_filters_by_workspace_even_where_rls_does_not_apply`                                                                                                                                                                                                                                                          |
| M8  | RLS chỉ theo tenant                                | `test_another_workspace_of_the_same_tenant_reads_none`                                                                                                                                                                                                                                                                                                                                                 |
| M9  | FK không có workspace                              | `test_a_document_cannot_be_written_into_another_workspace_than_its_case`                                                                                                                                                                                                                                                                                                                               |
| M10 | `dw_app` giữ DELETE                                | `test_the_application_may_only_read_and_add_documents`                                                                                                                                                                                                                                                                                                                                                 |
| M11 | CHECK `doc_type` thiếu `maquette`                  | `test_the_doc_type_check_is_exactly_the_domains_list`                                                                                                                                                                                                                                                                                                                                                  |
| M12 | quét bỏ qua tuổi một ngày                          | `test_a_fresh_object_without_a_row_is_kept`                                                                                                                                                                                                                                                                                                                                                            |
| M13 | quét xóa khóa sai dạng                             | `test_a_key_it_cannot_read_is_skipped_and_logged_never_deleted`                                                                                                                                                                                                                                                                                                                                        |
| M14 | quét hỏi sai scope                                 | `test_an_object_with_its_row_is_kept`                                                                                                                                                                                                                                                                                                                                                                  |
| M15 | offboarding không xóa chứng từ                     | `test_both_tenant_prefixed_buckets_are_exported_and_emptied_for_that_tenant_only`                                                                                                                                                                                                                                                                                                                      |
| M16 | fingerprint multipart bỏ `sha256`                  | `test_the_same_key_with_other_bytes_is_a_conflict`                                                                                                                                                                                                                                                                                                                                                     |
| M17 | operation chưa claim vẫn release khóa              | `test_an_operation_that_never_claimed_the_key_never_releases_it`                                                                                                                                                                                                                                                                                                                                       |
| M18 | tải xuống bỏ `nosniff`                             | `test_a_download_is_an_attachment_of_the_stored_type_never_sniffed`                                                                                                                                                                                                                                                                                                                                    |
| M19 | `sc_process_admin` có cả document.write            | chạy lại đúng như mô tả ở vòng review 1 (thêm `sc_process_admin` vào riêng UPDATE write của `f6a8142a6cd2`): đỏ ở `test_every_role_reads_documents_and_only_the_operating_roles_add_them`, `test_no_single_role_both_sets_the_rules_and_runs_cases`, `test_roles_without_a_conflict_can_be_held_together[roles4]`; `test_every_supply_chain_role_can_at_least_read` **không** đỏ (ghi ở vòng 1 là sai) |
| M20 | bỏ đổi vi phạm UNIQUE thành 409 theo tên           | `test_two_concurrent_uploads_never_share_a_version`                                                                                                                                                                                                                                                                                                                                                    |
| M21 | tên ASCII dự phòng không bỏ ký tự nguy hiểm        | `test_document_routes.py::test_a_name_cannot_end_the_header_or_its_quoted_string`                                                                                                                                                                                                                                                                                                                      |
| W1  | web: thiếu scope không khóa gì                     | vòng 1 **xanh** (nút vốn khóa vì chưa chọn file); đã gom khóa về một chỗ và kiểm cả ô chọn loại, nút chọn file; vòng 2 đỏ: `without the write scope, ...`                                                                                                                                                                                                                                              |
| W2  | web: bỏ lý do bằng chữ                             | `without the write scope, upload is disabled and says why in words`                                                                                                                                                                                                                                                                                                                                    |
| W3  | web: thử lại tạo khóa idempotency mới              | `a retry of the same file reuses its key; another file gets a new one`                                                                                                                                                                                                                                                                                                                                 |

### 2026-10-05 — vòng review 1 (implementer): 12 phát hiện, xử lý từng cái

Mọi phát hiện đều kiểm lại trên code trước khi sửa; cả 12 đúng (ba phát hiện về bucket
trong compose là cùng một lỗi).

1. **Trần kích thước chạy sau bước tốn kém (major, đúng).** Đo trong FastAPI 0.141.1:
   `routing.py` gọi `await request.form()` trước `solve_dependencies` (xác thực nằm
   trong đó), Starlette ghi phần file ra file tạm không giới hạn. Sửa theo cách (a):
   route tải lên dùng route class `body_capped_route(max_bytes + 64 KiB)`
   (`document_routes.py`), từ chối 413 từ header khi `Content-Length` vượt mức hoặc thân
   không khai độ dài (`Transfer-Encoding`, chunked), trước khi parse và trước xác thực.
   Đo uvicorn 0.53 (h11 0.16 và httptools 0.8): gửi 5000 byte với `Content-Length: 10`,
   app chỉ nhận 10; gửi cả `Content-Length` lẫn `Transfer-Encoding`, h11 đọc theo chunked
   (route từ chối vì có `Transfer-Encoding`), httptools trả 400. Vậy độ dài đã khai là
   giới hạn thật. Thân không khai độ dài bị trả 413 (không có mã 411 trong `ErrorCode`;
   trình duyệt và httpx luôn gửi độ dài cho form). Sửa ba chú thích sai
   (`document_routes.py`, `case_documents.py`, `settings.py`). ADR 0021 sửa đổi điểm 6
   ghi thêm. Test: `test_an_oversized_or_unbounded_body_is_413_before_auth_and_never_read`
   (gọi ASGI trực tiếp, không header `Authorization`, đếm số lần đọc body: 0).
2. **Khóa đối tượng lộ trong thân lỗi (đúng).** `MinioCaseDocumentStorage` không còn đưa
   `key` vào `details`; khóa chỉ ghi vào log. Test:
   `test_a_storage_failure_on_download_keeps_the_object_key_on_the_server` (adapter thật
   trên client S3 giả: thiếu đối tượng → 404, S3 lỗi → 503; thân không có
   `supply_chain/` hay id tenant). Fake trong unit test cũng thôi đưa khóa vào `details`.
3. **Tên bucket trong compose (ba phát hiện, đúng).** `CASE_DOCUMENTS_BUCKET` giờ được
   truyền cho cả `api` và `worker`, cùng biểu thức với `s3-setup`. Test
   `apps/worker/tests/unit/test_case_documents_bucket_config.py` đọc compose, đòi ba
   service cùng một giá trị và `s3-setup` tạo đúng bucket đó. Chú thích `.env.example`
   giờ đúng; ADR 0021 sửa đổi điểm 4 ghi rõ.
4. **Tenant "giống" không bắt được lỗi thiếu `/` (đúng).** Hai UUID cùng độ dài khác ký
   tự cuối không bao giờ là tiền tố của nhau. Thay bằng khóa `feedback/{A}x/...` và
   `supply_chain/{A}x/...` ở cả unit và integration; sửa chú thích.
5. **`NewCaseDocument.case_kind` không ai đọc (đúng).** Bỏ trường; S1 thêm lại cùng cột
   nó chọn. `CaseDocument.case_kind` (phía đọc) giữ: nó đúng theo cột đã đọc.
6. **`RequireFormIdempotency` không ai dùng (đúng).** Bỏ alias; docstring và chú thích
   chỉ tới `get_form_idempotent_operation`.
7. **Guard quyết định 5 không bao giờ sai (đúng).** Xem điểm 5 ở trên. Bỏ
   `if minio is not None` (early return giờ là
   `if minio is None or object_storage is None`, thu hẹp kiểu cho cả hai), bỏ
   `test_without_object_storage_no_case_document_handler_is_wired`; sửa chú thích ở
   `container.py`, `main.py`, `document_routes.py`.
8. **M19 ghi sai test đỏ (đúng).** Đã chạy lại đúng như mô tả; bảng mutation đã sửa.
9. **Không test nào giữ `read(max_bytes + 1)` (đúng).** Test
   `test_the_route_holds_at_most_one_byte_past_the_cap_in_memory` ghi lại kích thước mỗi
   lần `UploadFile.read` được gọi.
10. **Test "audit commit cùng dòng" không thể đỏ (đúng).** Test
    `test_a_refused_row_leaves_no_audit_and_a_refused_audit_leaves_no_row`: chèn bị FK
    từ chối → không có audit; audit bị RLS từ chối (tenant khác) → không có dòng.

**Mutation vòng này (control xanh trước; script áp đúng một thay thế, chạy, khôi phục và
so sha256 với bản gốc):**

| #   | Bỏ guard                                                       | Test đỏ                                                                                                                                                                    |
| --- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M22 | bỏ `route_class_override` (không còn trần ở header)            | `test_an_oversized_or_unbounded_body_is_413_before_auth_and_never_read` (cả hai tham số)                                                                                   |
| M23 | `file.read()` thay `file.read(max_bytes + 1)`                  | `test_the_route_holds_at_most_one_byte_past_the_cap_in_memory`                                                                                                             |
| M24 | `NotFoundError` của adapter đưa lại `key` vào `details`        | `test_a_storage_failure_on_download_keeps_the_object_key_on_the_server[object-missing]`                                                                                    |
| M25 | bỏ `CASE_DOCUMENTS_BUCKET` khỏi service `worker` trong compose | `test_the_bucket_job_the_api_and_the_worker_read_one_name`                                                                                                                 |
| M26 | `ObjectKey.tenant_prefix` bỏ `/` cuối                          | unit `test_both_tenant_prefixed_buckets_are_exported_and_emptied_for_that_tenant_only`; integration `test_offboarding_empties_tenant_a_in_both_buckets_and_keeps_tenant_b` |
| M27 | tiền tố `feedback/{tenant_id}` bỏ `/` cuối                     | cùng hai test như M26                                                                                                                                                      |
| M28 | audit ghi trước, trong giao dịch riêng                         | `test_a_refused_row_leaves_no_audit_and_a_refused_audit_leaves_no_row` (chỉ test này)                                                                                      |
| M29 | audit ghi sau, trong giao dịch riêng                           | `test_a_refused_row_leaves_no_audit_and_a_refused_audit_leaves_no_row` (chỉ test này)                                                                                      |
| M19 | chạy lại, xem bảng mutation ở trên                             | `test_every_role_reads_documents_and_only_the_operating_roles_add_them` và hai test SoD                                                                                    |

**Lệnh đã chạy vòng này (5/10/2026, Windows, Git Bash):**

| Lệnh                                                                                   | Kết quả                                                                         |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `uv run ruff check .`; `uv run ruff format --check .`                                  | xanh (624 file)                                                                 |
| `pnpm run format:check`                                                                | **đỏ** chỉ ở hai file lát A như trước (không đổi từ HEAD); file của lát này đạt |
| `pnpm run -r --if-present lint`                                                        | xanh                                                                            |
| `uv run mypy`; `pnpm run -r --if-present typecheck`                                    | xanh: mypy 456 file; tsc các gói                                                |
| `uv run pytest -m unit`                                                                | xanh: 1572 passed, 3 skipped                                                    |
| `make test-architecture` (ba lệnh)                                                     | xanh: import-linter 9 kept, 0 broken; declared-dependency 12 gói; invariants ok |
| `uv run pytest -m contract`                                                            | xanh: 5 passed (snapshot OpenAPI không đổi)                                     |
| `make release-manifest-check`                                                          | xanh                                                                            |
| `make eval-smoke`                                                                      | xanh: platform 4/4, supply_chain 22/22                                          |
| `pytest -m integration packages/python/dw_supply_chain/tests apps/worker/tests` (.env) | xanh: 118 passed                                                                |
| `pytest -m integration packages/python/dw_platform/tests` (.env)                       | xanh: 199 passed                                                                |
| `pnpm --filter @dw/web exec vitest run components/supply-chain`                        | xanh: 9 (web không đổi ở vòng này)                                              |

### 2026-10-05 — vòng review 2 (implementer): 3 phát hiện, cả ba đúng

1. **`make lint` đỏ vì hai file lát A (đúng).** `prettier` chênh thật, không phải do CRLF
   (`.gitattributes` buộc LF; file trên đĩa là LF): `spec.md` lệch độ rộng cột bảng,
   `issues/01-required-scope.md` thiếu dòng trống cuối file. Đã `prettier --write` hai
   file đó; diff chỉ là khoảng trắng (4 dòng thêm, 3 dòng bỏ). Đây là file của lát A, sửa
   vì tiêu chí `make ci` xanh của lát này; nội dung không đổi.
2. **Ngưỡng trần header không được test nào giữ (V09, đúng).** Test cũ chỉ gửi
   `Content-Length` 10 GiB. Thêm tham số `declared-one-byte-over-the-cap`
   (`max_bytes + MULTIPART_OVERHEAD_BYTES + 1` → 413, không đọc body) và test
   `test_a_body_declared_at_exactly_the_cap_is_read` (đúng bằng trần: qua kiểm header,
   body được đọc, không 413). Hàm trợ giúp ASGI đổi tên thành `_post_counting_body_reads`,
   nhận thêm một body hữu hạn.
3. **Mệnh đề `Transfer-Encoding` không được test nào giữ (V10, đúng).** Tham số
   `undeclared-length` không có `Content-Length`, nên `isdigit()` đã từ chối trước. Thêm
   tham số `declared-length-and-chunked` (`Content-Length: 10` cùng
   `Transfer-Encoding: chunked`, trường hợp h11 đọc theo chunked) → 413, không đọc body.

Code của `body_capped_route` không đổi; chỉ thêm test.

**Mutation vòng này** (control xanh trước; script áp đúng một thay thế vào
`document_routes.py`, chạy hai test của trần header, khôi phục, so nội dung với bản gốc:
khớp):

| #    | Bỏ guard                                    | Test đỏ                                                                                                 |
| ---- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| V09  | `int(declared) > limit * 1000`              | `test_an_oversized_or_unbounded_body_is_413_before_auth_and_never_read[declared-one-byte-over-the-cap]` |
| V10  | bỏ `"transfer-encoding" in request.headers` | `test_an_oversized_or_unbounded_body_is_413_before_auth_and_never_read[declared-length-and-chunked]`    |
| V09b | `int(declared) >= limit` (lệch một)         | `test_a_body_declared_at_exactly_the_cap_is_read`                                                       |

**Lệnh đã chạy vòng này (5/10/2026, Windows, Git Bash):**

| Lệnh                                                                                          | Kết quả                                                                         |
| --------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `make ci` (một lần chạy: lint, typecheck, unit, architecture, contract, eval-smoke, manifest) | xanh: "local CI gate passed"                                                    |
| `make lint`                                                                                   | xanh: ruff, ruff format (624 file), prettier "All matched files", lint các gói  |
| `make typecheck`                                                                              | xanh: mypy 456 file; tsc các gói                                                |
| `make test-unit`                                                                              | xanh: 1575 passed, 3 skipped (thêm 3 test)                                      |
| `make test-architecture`                                                                      | xanh: import-linter 9 kept, 0 broken; declared-dependency 12 gói; invariants ok |
| `make test-contract`                                                                          | xanh: 5 passed                                                                  |
| `make release-manifest-check`                                                                 | xanh                                                                            |
| `make eval-smoke`                                                                             | xanh: platform 4/4, supply_chain 22/22                                          |
| `pytest -m integration packages/python/dw_supply_chain/tests apps/worker/tests` (.env)        | xanh: 118 passed                                                                |
| `pytest -m integration packages/python/dw_platform/tests` (.env)                              | xanh: 199 passed                                                                |
| `pnpm --filter @dw/web exec vitest run`                                                       | xanh: 19 file, 102 test (web không đổi ở vòng này)                              |

- 2026-10-05, lead: verifier độc lập xanh trên toàn bộ `make ci` (unit 1575), integration dw_supply_chain 117, dw_platform 199, worker S3 thật 1, vitest 102, một head `f6a8142a6cd2`; 54/54 mutation đỏ rồi khôi phục. Hai lỗi review mức major (cap kích thước chạy sau khi parse multipart; compose không truyền tên bucket cho api, worker) đã sửa trong vòng fix. `make ci` đỏ trước đó chỉ vì prettier trên hai file plan của lát A, đã sửa.
