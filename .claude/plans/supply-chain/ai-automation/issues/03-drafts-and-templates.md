# 03 — Bản nháp và mẫu chứng từ

Status: ready-for-agent
Blocked by: —
Area: supply-chain

## Mục tiêu

Một bản nháp chứng từ có trường, có nguồn, dựng được thành DOCX/PDF từ mẫu có phiên bản, sửa được trên trang hồ sơ, và chỉ thành chứng từ khi được duyệt.

## Việc cần làm

1. **Migration:** `document_drafts` (hồ sơ, `doc_type` đích, phiên bản bản nháp, `fields
jsonb`, `gaps jsonb`, `sources` (id chứng từ + sha256 + id trích xuất), prompt và mẫu
   phiên bản, `object_key` file dựng, người tạo hoặc lane) chỉ thêm; `document_draft_decisions`
   (`confirmed | rejected | superseded`, người, lý do bắt buộc khi từ chối). Cột
   `case_documents.origin` CHECK `uploaded | ai_prepared` mặc định `uploaded`, `draft_id`
   FK nullable. Workspace RLS.
2. **Mẫu:** registry `doc_templates` (`configs/doc_templates/supply_chain/<id>@<semver>.docx`
    - `<id>@<semver>.yaml` khai trường), qua `TenantOverlay` (override tải lên lưu trữ, không
      cần deploy), ghim trong release manifest. Mẫu trung tính đầu: phiếu chỉnh sửa, biên bản
      đánh giá, tờ trình BGĐ, BM04, PO.
3. **Dựng:** port `DocumentRendererPort`; adapter chạy một script cố định trong sandbox
   docgen (python-docx điền trường, LibreOffice ra PDF); không script do mô hình viết; trường
   là dữ liệu, escape.
4. **Web:** khối "Bản nháp" trên trang hồ sơ: trường, khoảng trống tô màu, nguồn từng
   trường, xem trước file, sửa trường (tạo phiên bản bản nháp mới).
5. **Luật:** bản nháp không bao giờ thỏa điều kiện chứng từ của bước (domain chỉ đọc
   `case_documents`).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Bước đòi BM04 với chỉ một bản nháp BM04 chưa duyệt bị từ chối 409 (đột biến: cho domain đọc bản nháp thì đỏ).
- [ ] Override mẫu của tenant A không dùng cho tenant B (fallback về nền tảng, không sang ngang).
- [ ] Trường chứa `{{`, công thức Excel, macro không thực thi khi dựng.
- [ ] Sửa trường tạo phiên bản mới; phiên bản cũ còn đọc được.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0025 (E14) điểm 4; `apps/docgen`; `dw_agent_runtime/adapters/docgen_client.py`; `dw_kernel.overlay.TenantOverlay`.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
