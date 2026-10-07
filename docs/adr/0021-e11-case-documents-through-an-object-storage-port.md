---
status: Accepted
date: 2026-10-05
source:
    - ../products/elmich/process.md#2-danh-mục-tài-liệuchứng-từ-nghiệp-vụ-chính
    - ../../packages/python/dw_platform/src/dw_platform/application/ports.py # FeedbackAttachmentStoragePort dòng 212
    - ../../CLAUDE.md # Tenancy: object paths include tenant/workspace
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
