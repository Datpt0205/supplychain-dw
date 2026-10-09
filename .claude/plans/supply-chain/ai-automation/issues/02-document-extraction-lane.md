# 02 — Lane trích xuất chứng từ (ADR 0021 sửa đổi 2026-10-09)

Status: ready-for-agent
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

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Chứng từ id của workspace khác trong hàng đợi: lane từ chối, 0 lượt gọi mô hình.
- [ ] File có `</input>` và "bỏ qua hướng dẫn" vẫn nằm trong khối `<input>` (escape).
- [ ] File không đọc được trả `[không đọc được nội dung]` → `unreadable`, không trường.
- [ ] Số tài khoản trong file không có trong request gửi gateway (test bắt request).
- [ ] Trường mô hình trả mà trích dẫn không có trong văn bản → khoảng trống (đột biến: bỏ kiểm trích dẫn thì đỏ).
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0021 sửa đổi 2026-10-09; ADR 0010; `dw_knowledge` file parser; `configs/policies/attachment_ingest@1.1.0.yaml` (dấu rỗng).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
