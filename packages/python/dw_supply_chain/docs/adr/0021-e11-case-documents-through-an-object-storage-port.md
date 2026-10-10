---
status: Accepted
date: 2026-10-05
source:
    - ../../../../../docs/products/elmich/process.md#2-danh-mục-tài-liệuchứng-từ-nghiệp-vụ-chính
    - ../../../dw_platform/src/dw_platform/application/ports.py # FeedbackAttachmentStoragePort dòng 212
    - ../../../../../CLAUDE.md # Tenancy: object paths include tenant/workspace
---

# E11. Chứng từ của hồ sơ đi qua một port lưu trữ đối tượng

Quy trình Elmich có chín loại tài liệu (process.md mục 2) cùng email xác nhận của NCC
ở bước 8. Hồ sơ PO hôm nay không lưu chứng từ nào; chỉ cập nhật NCC có trích dẫn
nguyên văn.

**Quyết định:**

- Bảng `supply_chain.case_documents`: `tenant_id`, `workspace_id`, hai FK nullable
  `product_dev_case_id` và `po_case_id` (mỗi cái `ON DELETE RESTRICT`, có index) với
  CHECK đúng một cái khác NULL; `doc_type` (CHECK theo danh sách dưới); `object_key`;
  tên file, `content_type`, kích thước, `sha256`; `version`; `uploaded_by`,
  `uploaded_at`. RLS FORCE.
- `version` tăng theo `(hồ sơ, doc_type)` và database giữ nó duy nhất: UNIQUE riêng
  phần `(tenant_id, po_case_id, doc_type, version) WHERE po_case_id IS NOT NULL` và
  tương tự cho `product_dev_case_id`. Hai lần tải lên đồng thời cùng loại không ra cùng
  phiên bản: lần thua gặp vi phạm ràng buộc, adapter đổi thành 409 theo tên ràng buộc
  (mẫu tách nhiệm, `code-quality.md`), client thử lại.
- `doc_type`: `proposal_list`, `product_image` (ảnh SP ở bước 1), `sample_photo`, `sample_evaluation`,
  `sample_revision_request`, `product_profile_bm04`, `official_item_code`,
  `supplier_confirmation_email`, `purchase_order`, `deposit_docs`, `payment_docs`,
  `packaging_content`, `user_manual`, `maquette`.
- `object_key` do server dựng: `supply_chain/{tenant_id}/{workspace_id}/{case_kind}/{case_id}/{document_id}`.
  Không có phần nào lấy từ request.
- Port `CaseDocumentStoragePort` (put, get, delete theo khóa) do `dw_supply_chain`
  khai, adapter trên client S3 sẵn có (SeaweedFS ở compose), theo mẫu
  `FeedbackAttachmentStoragePort`.
- Tải lên có trần kích thước và danh sách `content_type` cho phép; tải xuống đi qua
  API sau khi đọc dòng dưới RLS, không phát URL ký sẵn.
- **Không để đối tượng mồ côi.** Tải lên ghi đối tượng trước rồi mới chèn dòng; khi
  giao dịch chèn dòng thất bại, handler xóa đối tượng vừa ghi (best-effort, có log).
  Một lượt quét ở lane `retention` xóa khóa dưới `supply_chain/` không có dòng nào và
  cũ hơn một ngày (failure-modes #6), cho trường hợp tiến trình chết giữa hai bước.
- Chứng từ chỉ thêm phiên bản, không sửa, không xóa bằng tay. Offboarding xuất và
  xóa đối tượng theo tiền tố `supply_chain/{tenant_id}/`, như đã làm với
  `feedback/{tenant_id}/`.
- Nội dung file không đưa vào mô hình trong các lát này; file là dữ liệu không tin cậy.

## Phương án đã cân nhắc

- **Một cột `case_id` đa hình với `case_kind`.** Bác: không FK được, trái quy tắc mọi
  FK có `ON DELETE` và index.
- **Lưu file trong PostgreSQL.** Bác: phình bản sao lưu; object storage đã có.

## Hệ quả

- Bước nào bắt buộc chứng từ nào là điểm mở (QE-02). Các lát giai đoạn 1 đòi chứng từ
  ở bước 3 (đạt), 4, 7 và 8 vì PDF ghi chúng là đầu ra; bước khác chưa đòi.

## Sửa đổi 2026-10-05 (tạm, lát D; chờ Đạt duyệt ở QO-2)

Các quyết định tạm của lead khi làm lát D. Chúng thay hoặc làm rõ phần Quyết định ở trên:

1. **FK `ON DELETE CASCADE`, không `RESTRICT`.** FK là
   `(tenant_id, workspace_id, po_case_id)` tới `po_cases (tenant_id, workspace_id, id)`,
   có index riêng. `dw_app` chỉ có SELECT và INSERT trên `case_documents` (không UPDATE,
   DELETE, TRUNCATE). Lý do: offboarding chỉ xóa bảng mà `dw_app` được DELETE, rồi xóa
   `po_cases`; với `RESTRICT` cộng việc không có DELETE, lần xóa `po_cases` của một
   tenant có chứng từ sẽ thất bại và offboarding kẹt. Cùng cách với `follow_ups`
   (`dc2285c629d4`, `bc3f0c1279fd`). CASCADE từ `po_cases` là **đường duy nhất** một
   dòng chứng từ rời bảng; không ai xóa tay được. Đối tượng trong bucket đi theo tiền tố
   `supply_chain/{tenant_id}/` ở lane offboarding của worker. Còn mở: thời hạn lưu
   chứng từ theo luật cho `payment_docs` và `deposit_docs` (thêm vào QE-02).
2. **Workspace.** `case_documents` là bảng đầu tiên thu hẹp theo workspace, RLS ENABLE
   và FORCE với đúng dạng của `CLAUDE.md`:
   `tenant AND (workspace OR app.workspace_scope = 'tenant')`. Workspace của chứng từ
   là workspace của hồ sơ:
   thêm UNIQUE `(tenant_id, workspace_id, id)` trên `po_cases` cho FK ghép. Handler tải
   lên và liệt kê đọc hồ sơ rồi trả 404 khi workspace của hồ sơ khác workspace của người
   gọi, trước khi ghi bất kỳ đối tượng nào. RLS của `po_cases` không đổi (chỉ theo tenant).
3. **`po_case_id` NOT NULL ở lát D.** S1 bỏ NOT NULL, thêm `product_dev_case_id` và CHECK
   đúng một FK khác NULL. Có sẵn UNIQUE `(tenant_id, workspace_id, id)` trên
   `case_documents` cho FK ghép của các bảng S1.
4. **Bucket riêng** `case-documents` (setting `case_documents_bucket`, biến môi trường
   chung `CASE_DOCUMENTS_BUCKET`; compose truyền cùng một biến cho `s3-setup`, `api` và
   `worker`, và `s3-setup` tạo sẵn bucket). Khóa là
   `supply_chain/{tenant_id}/{workspace_id}/po/{case_id}/{document_id}`; `{case_kind}`
   của khóa là `po` ở lát này. CHECK `ck_case_documents_object_key` buộc khóa đúng bằng
   khóa dựng từ chính các id của dòng.
5. **Quét mồ côi là lane riêng** `supply_chain_document_orphans` của worker, chạy theo
   nhịp retention (mỗi giờ), không nằm trong lane `retention`. Mỗi lượt đọc một trang có
   giới hạn; với khóa cũ hơn một ngày, đọc tenant và workspace từ chính khóa, hỏi
   database trong phiên tenant thường (không policy chéo tenant, không
   `app.workspace_scope`), xóa khi không có dòng. Khóa không đọc được thì ghi log và bỏ
   qua, không bao giờ xóa.
6. **Lỗi:** quá trần là 413 (`payload_too_large`), loại file ngoài danh sách hoặc nội
   dung không khớp loại đã khai là 415 (`unsupported_media_type`). Loại file quyết bằng
   `content_type` khai báo cùng chữ ký đầu file (PDF, PNG, JPEG, ZIP cho XLSX/DOCX, OLE
   cho MSG); EML không có chữ ký, nhận theo loại khai báo. Trần mặc định 25 MiB
   (`case_document_max_bytes`). FastAPI parse form trước mọi dependency, kể cả xác
   thực, và Starlette ghi cả phần file ra file tạm; vì vậy route tải lên từ chối bằng
   413 ngay từ header, trước khi parse, khi `Content-Length` vượt trần cộng 64 KiB cho
   khung multipart hoặc khi thân không khai độ dài (chunked). Handler giữ đúng trần trên
   chính file.
7. **Tải xuống** đọc cả file vào bộ nhớ (dưới trần), port không có đọc dạng stream. Phản
   hồi luôn là `attachment` với `filename*` (RFC 5987) và tên ASCII dự phòng,
   `X-Content-Type-Options: nosniff`, `Content-Type` là loại đã lưu. Tải xuống không
   ghi audit (không bản ghi nào cùng loại có audit khi đọc); tải lên ghi audit trong
   cùng giao dịch với dòng.
8. **Scope:** `supply_chain.document.read` cho cả bảy vai `sc_*`;
   `supply_chain.document.write` cho năm vai vận hành, và nằm ở phía vận hành của
   `sod_sc_rules_vs_operations`, nên `sc_process_admin` không tải lên được.

## Sửa đổi 2026-10-05 (tạm, lát S1; chờ Đạt duyệt ở QO-2)

Lát S1 thêm loại hồ sơ thứ hai, đúng như sửa đổi lát D (điểm 3) đã báo:

1. `po_case_id` bỏ NOT NULL; thêm `product_dev_case_id` với FK ghép
   `(tenant_id, workspace_id, product_dev_case_id)` tới `product_dev_cases` `ON DELETE
CASCADE` (không `RESTRICT`, cùng lý do điểm 1 của lát D); CHECK
   `ck_case_documents_one_case` (đúng một FK khác NULL); UNIQUE riêng phần phiên bản
   `uq_case_documents_tenant_id_product_case_doc_type_version`; UNIQUE
   `(tenant_id, workspace_id, product_dev_case_id, id)` là đích FK của vòng mẫu và phiếu
   chỉnh sửa, nên một chứng từ chỉ đóng được vòng của chính hồ sơ nó thuộc.
2. `{case_kind}` của khóa là `po` hoặc `product`
   (`supply_chain/{tenant}/{workspace}/product/{case_id}/{document_id}`); CHECK
   `ck_case_documents_object_key` phủ cả hai. Quét mồ côi và offboarding đi theo tiền tố,
   không đổi.
3. Cùng ba handler của lát D phục vụ cả hai loại: mỗi loại hỏi repository của nó
   "hồ sơ này ở workspace nào" (`CaseLookupPort.case_workspace`), chọn theo `CaseKind`.
   Route `POST`/`GET /product-cases/{id}/documents` cạnh route của PO. API trả `case_kind`
   và `case_id` thay cho `po_case_id`.
4. Chứng từ đã đóng một vòng mẫu không xóa riêng được (FK `NO ACTION` từ vòng); hồ sơ bị
   xóa (chỉ offboarding) thì chứng từ và vòng đi cùng trong một câu lệnh.

## Sửa đổi 2026-10-06 (hạn lưu chứng từ kế toán)

`deposit_docs`, `payment_docs` và `purchase_order` là chứng từ kế toán; Luật Kế toán 2015
và Nghị định 174/2016/NĐ-CP đòi lưu tối thiểu 10 năm. Nền tảng không tự xóa chứng từ của
hồ sơ: chứng từ chỉ rời đi khi tenant bị offboard, và bundle offboarding trao lại cho chủ
dữ liệu trước khi xóa. Nghĩa vụ lưu thuộc Elmich, chủ sở hữu hồ sơ.

## Sửa đổi 2026-10-07 (lát P4; nhất quán với ADR 0017)

Câu "RLS của `po_cases` không đổi (chỉ theo tenant)" ở điểm 2 lát D không còn đúng:
`62cdcf3bf2d2` hẹp `po_cases` theo workspace (ADR 0017, sửa đổi P4). Phép kiểm
`CaseLookupPort.case_workspace` của handler giữ nguyên, làm lớp thứ hai: với Hồ sơ PO của
workspace khác, RLS đã trả "không có", và handler vẫn trả 404 trước khi ghi đối tượng nào.

## Sửa đổi 2026-10-07 (tạm, lát PK; Đạt ủy quyền quyết các điểm mở)

Ba loại chứng từ của sơ đồ con bước 12 thêm vào danh sách: `colour_sample` (mẫu màu),
`packaging_design` (thiết kế bao bì), `pre_production_test_report` (biên bản test trước
SX). Một chủ của danh sách vẫn là `DocumentType` trong `dw_supply_chain`; CHECK
`ck_case_documents_doc_type` (migration `85659fd91943`) lặp lại và test integration so
bằng nhau; enum zod của web so `SameType` với kiểu sinh từ OpenAPI nên lệch thì không
biên dịch. Bước test trước SX lấy biên bản của chính Hồ sơ PO, tải lên từ khi nhận mẫu
trước SX, ghi trên dòng lịch sử với FK `ON DELETE NO ACTION` như bước 7–8. Chi tiết ở
Comments của `.claude/plans/supply-chain/packaging-design/issues/01-colour-packaging-and-pre-production.md`.

## Sửa đổi 2026-10-09 (Đạt; E14): nội dung file đến mô hình như dữ liệu không tin cậy

Câu "Nội dung file không đưa vào mô hình trong các lát này" được thay bằng các điều kiện
dưới đây; ngoài chúng, nội dung file vẫn không đến mô hình.
[ADR 0025](0025-e14-ai-prepares-a-step-a-person-approves-the-move.md) nói vì sao.

1. **Chỉ ở lane trích xuất của worker** (`supply_chain_document_extraction`), không bao giờ
   trong request API. Tenant và workspace lấy từ dòng chứng từ đọc dưới RLS, không từ
   request; chứng từ của hồ sơ khác hoặc workspace khác bị từ chối trước mọi lượt gọi.
2. **Như dữ liệu:** văn bản trích là một biến `<input>` của prompt (ADR 0010), không bao
   giờ là chỉ dẫn. File thành văn bản qua cùng đường của `dw_knowledge` (PDF, DOCX, XLSX
   nhờ mô hình "chép nguyên văn", ảnh qua `input_image`, EML/MSG đọc thân và tệp đính kèm
   bằng code); dấu `[không đọc được nội dung]` làm lượt trích thất bại có tên.
3. **Kết quả kiểm:** schema theo `doc_type`; mỗi trường mang trích dẫn nguyên văn mà code
   tìm thấy trong văn bản (luật của cập nhật NCC). Trường không có trích dẫn tìm thấy là
   khoảng trống, không phải giá trị.
4. **Không quyết:** một trích xuất chỉ điền bản nháp hoặc một phát hiện mà người duyệt.
5. **Số tài khoản ngân hàng** và mã định danh tương tự bị che trước khi gọi; code so chúng
   với danh mục NCC. Nội dung khác được gửi tới nhà cung cấp mô hình (Đạt, 9/10/2026).
6. **Tính lượt:** mọi lượt gọi qua `DailyAllowance` và sổ chi tiêu; hết lượt thì không
   trích, chứng từ vẫn lưu.
7. **Lưu:** `document_extractions` theo (id chứng từ, sha256), cùng workspace RLS, chỉ thêm;
   đổi phiên bản prompt thì trích lại thành dòng mới.

## Sửa đổi 2026-10-09 (tạm, lát AI-02; cách đọc khác thiết kế ở điểm 2)

Lát AI-02 làm lane trích xuất. Ba chỗ khác điểm 2 và 6 của sửa đổi trên, ghi lại để Đạt
duyệt:

1. **Văn bản đọc trong tiến trình, không nhờ mô hình chép.** PDF (lớp chữ, `pypdf`), DOCX
   (`python-docx`), XLSX (`openpyxl`, giá trị đã lưu, không tính công thức) và EML (thân
   và tệp đính kèm đọc được) do `dw_knowledge.adapters.office_parsers` đọc, không gọi mô
   hình. Lý do: điểm 5 đòi che số tài khoản **trước** khi gọi; một file đưa nguyên cho
   mô hình "chép nguyên văn" thì mô hình đã thấy số tài khoản trước khi code kịp che.
2. **Ảnh, PDF quét (không có lớp chữ) và MSG là `unreadable`** ở lát này, không đoán.
   Đường `input_image` của `dw_knowledge` gửi file thẳng tới gateway: không che được số
   tài khoản trong ảnh, và không qua `DailyAllowance` lẫn sổ chi tiêu (điểm 6). Mở lại
   khi có một route ảnh của `ModelGateway` ghi chi tiêu, và chỉ cho loại chứng từ không
   mang tài khoản (không phải `deposit_docs`, `payment_docs`, báo giá). MSG cần một
   parser OLE (`extract-msg`), chưa thêm phụ thuộc.
3. **Hết lượt không ghi dòng.** Lỗi mô hình (sai schema) ghi `refused`, lỗi nhà cung cấp
   sau các lần thử của gateway ghi `failed`: mỗi chứng từ một dòng theo phiên bản prompt,
   không thử lại. Hết lượt trong ngày thì không có lượt gọi nào xảy ra và không ghi gì;
   tick sau hỏi lại (một lần đọc, không phải một lượt gọi), nên chứng từ được đọc khi có
   lượt, đúng "hết lượt thì không trích, chứng từ vẫn lưu".

Kèm theo: hàng đợi là hàm definer `supply_chain.documents_awaiting_extraction` (chỉ id);
mỗi chứng từ đọc dưới RLS của chính tenant và workspace của nó, và lane còn tự so tenant
và workspace của dòng với hàng đợi (lớp thứ hai). FK ghép của `document_extractions` gồm
cả `doc_type` và `sha256`, nên một dòng trích xuất không mang được loại hay hash khác
chứng từ của nó.

## Sửa đổi 2026-10-10 (tạm, lát AI-15; tài khoản thụ hưởng do code đọc)

1. Ba loại chứng từ mới: `proforma_invoice`, `commercial_invoice`, `bank_transfer_receipt` (UNC),
   CHECK của `case_documents` và `document_drafts` (migration `8e2d87208f42`).
2. Với loại có tài khoản thụ hưởng (`ExtractionSpec.reads_accounts`), code đọc số tài khoản từ văn
   bản **trước khi che** (số sau "STK", "số tài khoản", "account", "A/C"; IBAN; một dãy số trần
   dài vẫn bị che nhưng không coi là tài khoản, để số tiền viết liền không thành "tài khoản khác")
   và chỉ giữ SHA-256 của dạng chuẩn hóa (`beneficiary_accounts`), không giữ số. Mô hình chỉ thấy
   chỗ che; schema của lời đọc cấm khóa đó, nên mô hình không cài được digest.
3. Ảnh và PDF quét vẫn `unreadable` (chưa có OCR): PI, hóa đơn, UNC chụp ảnh không được đọc; trang
   nêu "máy không đọc được, người kiểm bằng mắt".

## Sửa đổi 2026-10-10 (lát AI-21; OCR cục bộ đã đo, chưa dùng)

Mục tiêu: ảnh và PDF quét đọc bằng OCR trong tiến trình, văn bản qua đúng đường che tài khoản và
grounding của PDF có lớp chữ. Đã đo trước khi dựa vào (ticket `ai-automation/21`): RapidOCR (có
trong lock qua extra `parsers`, cũng là OCR docling chọn) **không viết được chữ tiếng Việt**: từ
điển PP-OCRv6 thiếu 88/120 nguyên âm mang thanh, `latin` PP-OCRv5 thiếu 93; trên trang sạch chỉ
29–36% từ có dấu đúng, trong khi mọi dòng vẫn có điểm tin cậy ≥ 0,81, nên đánh dấu độ tin thấp
không bắt được lỗi, và grounding (kiểm trích dẫn trên chính văn bản OCR) chấp nhận một giá trị sai.

Quyết định tạm: **giữ điểm 2 của sửa đổi AI-02**: ảnh, PDF quét và MSG vẫn `unreadable`, không mã
nào thay đổi. Khi có một bộ OCR đạt ngưỡng của ticket (≥ 90% từ có dấu đúng trên trang sạch, điểm
tin cậy phân biệt được dòng sai; EasyOCR `vi` đo được 96–98%, chưa trong lock: Đạt quyết), văn bản
OCR đi qua `DocumentTextPort` như mọi văn bản khác: che trước lượt gọi, grounding sau; dòng điểm thấp
thành khoảng trống, không thành giá trị; với loại `reads_accounts`, digest tài khoản không đọc từ
văn bản OCR.

## Sửa đổi 2026-10-10 (Đạt; AI-21): ảnh và PDF quét đọc bằng Docling + EasyOCR

Đạt chọn **Docling làm đường đọc tài liệu, EasyOCR (`vi`) làm bộ OCR của nó**. Thay điểm 2 của sửa
đổi AI-02 và điểm 3 của sửa đổi AI-15 cho ảnh và PDF quét; MSG vẫn `unreadable`.

1. **Đo lại trên đúng đường sẽ chạy** (ticket `ai-automation/21`, cùng hai trang thật, ba biến thể):
   trang sạch 97% / 93% từ có dấu đúng nguyên văn (ngưỡng 90% đạt), quét tốt 96–98%; quét kém
   28–43%. Docling thêm thứ tự đọc và bảng: 76–84% từ đúng thứ tự trên trang sạch, so với 60–80%
   của EasyOCR đọc dòng rời (55–73% khi tắt mô hình bảng của Docling). PDF quét đi qua backend
   pdfium (backend mặc định của Docling chỉ được 77–80% trên cùng bản quét tốt).
2. **Điểm tin cậy từng dòng có qua Docling** (`parsed_page.textline_cells`), nhưng Docling mặc định
   **bỏ im lặng** mọi dòng dưới 0,5: bản quét kém khi đó báo 0 dòng nghi ngờ. Adapter đặt ngưỡng
   của Docling về 0, giữ mọi dòng với điểm của nó; quyết định thuộc về lane.
3. **Không đọc được:** văn bản có hơn 30% dòng điểm < 0,5 (`MAX_DOUBTFUL_SHARE`) là `unreadable`,
   không lượt gọi. Đo được: trang tốt 1–9% dòng nghi ngờ, quét kém 37–63%.
4. **Dòng nghi ngờ trong văn bản đạt:** trường có trích dẫn rơi vào dòng đó là khoảng trống
   `low_confidence`, không giữ giá trị. Dòng được tìm theo nguyên từ trong văn bản đã che; dòng
   không tìm thấy nguyên văn thì nghi ngờ ở mọi trích dẫn chứa nó hoặc nằm trong nó.
5. **Che trước lượt gọi** như PDF có lớp chữ, kể cả dòng nghi ngờ. Với loại `reads_accounts` (PI,
   hóa đơn, UNC) đọc từ OCR, code **không đọc digest tài khoản** (một chữ số đọc sai sẽ thành "tài
   khoản khác"): dòng đọc ghi `beneficiary_accounts_unread: "ocr"`, bước nêu `account_not_read` và
   người so tài khoản bằng mắt.
6. **Giới hạn:** tối đa 5 trang, 300 giây mỗi chứng từ (`DW_WORKER_OCR_MAX_PAGES`,
   `DW_WORKER_OCR_TIMEOUT_SECONDS`); quá thì `unreadable`. Lane đọc tuần tự, nên một bản quét 200
   trang không giữ được lane.
7. **Trọng số nướng vào image worker** (`/app/models/ocr`: layout, bảng, CRAFT, `latin_g2`);
   uat/production không khởi động khi thiếu `DW_WORKER_OCR_ARTIFACTS_PATH` hoặc thiếu docling, và
   Docling không tải gì khi có đường dẫn. Máy dev tải ở lần chạy đầu.
8. **Nơi đặt:** port `OcrPort` và adapter `DoclingOcrReader` ở `dw_knowledge` (nền tảng, để đưa lên
   `codebase` sau, ADR 0011); ngưỡng và cách dùng điểm tin cậy ở `dw_supply_chain`; nối ở gốc
   composition của worker. Lane đề xuất danh sách SP (AI-08) chưa dùng OCR.
