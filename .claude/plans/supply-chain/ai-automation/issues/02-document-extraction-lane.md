# 02 — Lane trích xuất chứng từ (ADR 0021 sửa đổi 2026-10-09)

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

Chứng từ tải lên được đọc thành văn bản và trường có kiểu, mỗi trường có trích dẫn nguyên văn, như dữ liệu không tin cậy, ở worker, không quyết gì.

## Việc cần làm

1. **Migration:** `document_extractions` (id chứng từ, sha256, `doc_type`, prompt id và
   phiên bản, profile mô hình, `text` đã trích, `fields jsonb`, `gaps jsonb`, trạng thái CHECK
   `extracted | unreadable | refused | failed`), FK ghép tới `case_documents`, UNIQUE
   (chứng từ, prompt phiên bản), workspace RLS, chỉ thêm.
2. **Lane** `supply_chain_document_extraction`: chứng từ mới → đọc dòng dưới RLS trong phiên
   tenant + workspace của chính dòng → file thành văn bản (PDF/DOCX/XLSX qua đường parser
   của `dw_knowledge`, ảnh `input_image`, EML/MSG bằng code: thân và tệp đính kèm) → che số
   tài khoản và định danh tương tự → lượt gọi có cấu trúc theo `doc_type`
   (`configs/prompts/supply_chain/extract_<doc_type>@1.0.0.yaml`, biến `<input>`) → code
   tìm từng trích dẫn trong văn bản, trường không tìm thấy thành khoảng trống. Lỗi và hết
   lượt ghi trạng thái, không thử vô hạn.
3. **Schema theo loại** trong domain (một chủ; loại chưa có schema thì chỉ lưu văn bản).
   Đợt đầu: `sample_evaluation`, `supplier_confirmation_email`, `product_profile_bm04`
   (file tải lên), báo giá/spec NCC (loại mới `supplier_quotation`).
4. **Tính lượt** qua `DailyAllowance` và sổ chi tiêu; audit như lane (ADR 0011).
5. Cập nhật `docs/products/elmich/process.md` và ADR 0021 nếu cách đọc EML/MSG khác thiết kế.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; xem Comments.)_
- [x] Chứng từ id của workspace khác trong hàng đợi: lane từ chối, 0 lượt gọi mô hình.
- [x] File có `</input>` và "bỏ qua hướng dẫn" vẫn nằm trong khối `<input>` (escape).
- [x] File không đọc được trả `[không đọc được nội dung]` → `unreadable`, không trường.
- [x] Số tài khoản trong file không có trong request gửi gateway (test bắt request).
- [x] Trường mô hình trả mà trích dẫn không có trong văn bản → khoảng trống (đột biến: bỏ kiểm trích dẫn thì đỏ).
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0021 sửa đổi 2026-10-09; ADR 0010; `dw_knowledge` file parser; `configs/policies/attachment_ingest@1.1.0.yaml` (dấu rỗng).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-02 (agent).** Đã làm (không Docker theo yêu cầu của Đạt):

- Migration `e21dc10d13b5` (hex của alembic, một head; đọc lại và parse bằng `pglast`
  qua `alembic upgrade/downgrade --sql`; **chưa chạy trên Postgres**):
  `document_extractions` (workspace RLS FORCE, chỉ SELECT + INSERT, UNIQUE theo (chứng
  từ, prompt, phiên bản), FK ghép `(tenant, workspace, document, doc_type, sha256)` tới
  `case_documents` nên dòng không mang được loại hay hash khác chứng từ, CHECK trạng thái
  và hình dạng), hàm definer `supply_chain.documents_awaiting_extraction` (chỉ id),
  `supplier_quotation` vào `ck_case_documents_doc_type` và `DocumentType` (web cũng thế).
- `domain/extraction.py`: một chủ của loại nào được đọc (`EXTRACTION_SPECS`), mô hình chỉ
  trả `{value, quote}`, code đọc số (cả hai quy ước, mơ hồ thì từ chối) và ngày, giữ một
  trường chỉ khi trích dẫn có trong văn bản VÀ giá trị có trong trích dẫn; số học báo giá
  do code (thành tiền, tổng); che IBAN, số sau "STK/số tài khoản/account/A/C", dãy 9-19
  chữ số và nhóm chữ số cách bằng dấu cách/gạch.
- `application/document_extraction.py`: lane `supply_chain_document_extraction` (worker,
  60 s): đọc chứng từ dưới RLS của chính tenant + workspace trong hàng đợi và tự so lại
  (lớp hai), gói của tenant (không có thì không gọi), hash của đối tượng, văn bản trong
  tiến trình, che, một lượt gọi qua one-call gateway (`DailyAllowance` + sổ chi tiêu),
  grounding, ghi dòng + audit của lane trong một giao dịch.
- `dw_knowledge.adapters.office_parsers` (ứng viên upstream): PDF lớp chữ, DOCX, XLSX,
  EML; phụ thuộc `pypdf`, `python-docx`, `openpyxl` vào `dw_knowledge` (uv.lock).
- Bốn prompt `extract_<doc_type>@1.0.0` (biến duy nhất `document_text`, registry giam).
- Eval `supply_chain@1.9.0` (82 ca, +17 qua grader `supply_chain.document_extraction`
  chạy lane thật, prompt thật): mỗi extractor có ca bình thường, chèn lệnh (khối `<input>`
  giả, lệnh không vào system, số tài khoản không tới mô hình), chứng từ tenant khác hoặc
  workspace khác cùng tenant (0 lượt gọi, không dòng), thiếu bằng chứng (khoảng trống);
  thêm ca tổng báo giá bịa (`number_mismatch`).
- ADR 0021 "Sửa đổi 2026-10-09 (tạm, lát AI-02)" ghi ba chỗ khác thiết kế (đọc trong tiến
  trình; ảnh, PDF quét, MSG là `unreadable`; hết lượt không ghi dòng).

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): eval — prompt không escape chứng
từ; lane tin chứng từ của workspace khác (adapter đọc chéo); không kiểm trích dẫn trong
văn bản; không kiểm số học báo giá; không che số tài khoản. Unit — lane tin chứng từ lạ;
không che trước khi gọi; số không kiểm trong trích dẫn; văn bản rỗng vẫn gửi; hash đối
tượng không kiểm; tenant không có gói vẫn gọi. 11/11 đỏ.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_document_extractions.py` (xuyên
tenant/workspace, FK loại + hash, một lần đọc mỗi phiên bản prompt, hàm hàng đợi chỉ id và
bỏ chứng từ đã đọc), `dw_platform/tests/integration/test_privileges.py::test_document_extractions_are_append_only_and_the_queue_is_executable`,
`test_case_documents.py` (CHECK `doc_type` bằng `DocumentType`, nay có `supplier_quotation`),
`test_rls_coverage.py` trên bảng mới, migration chạy thật (upgrade rồi downgrade), quét
image (`trivy`) sau khi thêm `pypdf`.

Chưa làm, ghi lại: ảnh, PDF quét và MSG (cần route ảnh của gateway có ghi chi tiêu, và
parser MSG); loại chứng từ chỉ lưu văn bản (chưa loại nào cần); API đọc dòng trích xuất
(AI-03/AI-05 dùng).
