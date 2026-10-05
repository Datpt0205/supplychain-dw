# 01 — `supply_chain.case_documents`, `CaseDocumentStoragePort`, route tải lên và tải xuống

Status: ready-for-agent
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

- [ ] **Test âm RLS:** tenant B không đọc dòng của A; workspace khác cùng tenant không
      đọc; `test_rls_coverage.py` xanh.
- [ ] **Test âm route:** tải lên vào hồ sơ của tenant khác trả 404 và không ghi đối
      tượng nào; tải xuống id của tenant khác trả 404; workspace khác trả 404.
- [ ] `object_key` luôn bắt đầu bằng `supply_chain/{tenant}/{workspace}/` của context
      truy cập; tên file chứa `../` không đổi khóa.
- [ ] File quá trần 413; `content_type` ngoài danh sách 415; hai lần POST cùng khóa
      idempotency tạo một phiên bản.
- [ ] Offboarding: integration test xóa đối tượng của tenant A, giữ của tenant B.
- [ ] Hai lần tải lên đồng thời cùng `(hồ sơ, doc_type)` (hai giao dịch thật): hai
      phiên bản khác nhau hoặc một 409, không bao giờ hai dòng cùng phiên bản. Mutation:
      bỏ UNIQUE thì test đỏ.
- [ ] Chèn dòng thất bại (lỗi tiêm ở repository giả): đối tượng vừa ghi bị xóa. Lượt quét
      xóa khóa không có dòng cũ hơn một ngày, giữ khóa có dòng và khóa mới.
- [ ] Mutation: bỏ tiền tố tenant trong `object_key` thì test đỏ (ghi vào Comments).
- [ ] `make ci` xanh; integration của `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 ("no documents table and no object storage per case"), mục 3
  (`CaseDocument`), mục 2 hàng 11, 14, 16.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "New tables"; câu "The platform already has ...
  `FeedbackAttachmentStoragePort` (`dw_platform/application/ports.py:212,235`)".
- `docs/products/elmich/process.md` mục 2.

## Comments
