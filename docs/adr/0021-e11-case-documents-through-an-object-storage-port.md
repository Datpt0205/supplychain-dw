---
status: Proposed
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
