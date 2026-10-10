# 03 — Bản nháp và mẫu chứng từ

Status: resolved
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

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; xem Comments.)_
- [x] Bước đòi BM04 với chỉ một bản nháp BM04 chưa duyệt bị từ chối 409 (đột biến: cho domain đọc bản nháp thì đỏ).
- [x] Override mẫu của tenant A không dùng cho tenant B (fallback về nền tảng, không sang ngang).
- [x] Trường chứa `{{`, công thức Excel, macro không thực thi khi dựng.
- [x] Sửa trường tạo phiên bản mới; phiên bản cũ còn đọc được.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(`make ci` xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0025 (E14) điểm 4; `apps/docgen`; `dw_agent_runtime/adapters/docgen_client.py`; `dw_kernel.overlay.TenantOverlay`.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-03 (agent).** Đã làm (không Docker theo yêu cầu của Đạt; dựng mẫu
trong tiến trình theo quyết định của Đạt, không qua docgen):

- Migration `cbebad572558` (hex của alembic, một head; đọc lại và parse bằng `pglast`;
  **chưa chạy trên Postgres**): `document_drafts` (một dòng mỗi phiên bản, chỉ thêm, đúng
  một hồ sơ, FK ghép `ON DELETE CASCADE` có index, UNIQUE `(tenant, lineage, version)`,
  `content_sha256`), `document_draft_decisions` (một quyết định mỗi phiên bản, lý do bắt
  buộc khi từ chối), `case_documents.origin` (`uploaded | ai_prepared`) + `draft_id` với
  CHECK hai cột khớp nhau, `doc_template_overrides` (theo tenant, như
  `platform.policy_overrides`), `bod_submission` vào `DocumentType` và CHECK. Workspace
  RLS FORCE cho hai bảng bản nháp, tenant RLS FORCE cho override; chỉ SELECT + INSERT.
- Nền tảng (ứng viên upstream): `dw_agent_runtime.doc_templates` (khai báo YAML + DOCX,
  `TenantOverlay`, kiểm khi nạp: trường khai báo đúng bằng placeholder, không macro,
  placeholder không bị tách run; `DocumentRendererPort`) và
  `dw_agent_runtime.adapters.docx_templates` (python-docx, điền một lượt: giá trị không
  bao giờ bị đọc lại như placeholder, không thành field code hay công thức; ký tự XML
  không mang được bị bỏ; ô thiếu in "[cần điền: …]"). `python-docx` vào `dw_agent_runtime`.
- Năm mẫu trung tính `configs/doc_templates/supply_chain/*@1.0.0.{yaml,docx}` (phiếu chỉnh
  sửa, biên bản đánh giá, tờ trình BGĐ, BM04, PO) từ `scripts/build_doc_templates.py`
  (không ghi đè phiên bản đã có); ghim trong release manifest (`doc_templates`).
- `domain/document_draft.py`, `application/document_drafts.py`: chuẩn bị (cửa của lane,
  ticket 05), đọc, danh sách, sửa thành phiên bản mới, từ chối có lý do, xem trước; giá
  ẩn khi thiếu `commercial.read` ở cả bản đọc lẫn bản xem trước, sửa giá cần
  `commercial.write`; override mẫu của tenant (`PUT /doc-templates`, `action_duties.write`).
- Web: `DraftsCard` trên cả hai trang hồ sơ (ô thiếu, nguồn từng trường "Máy đọc: …" hay
  "Người sửa", "AI soạn", giá khóa, tải bản xem trước, sửa trường vô hướng, từ chối có lý
  do; khóa kèm lý do khi đóng hay thiếu quyền).
- ADR 0025 "Sửa đổi 2026-10-09 (tạm, lát AI-03)": dựng trong tiến trình sau port, không lưu
  file dựng của bản nháp (xem trước dựng lại, approval gắn `content_sha256`), override
  trong PostgreSQL, `bod_submission`, chỉ từ chối ở lát này.

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): giá trị đã điền bị quét lại như
placeholder; mẫu có macro được nhận; placeholder tách run được nhận; giá hiện khi thiếu
`commercial.read`; bản xem trước in giá ẩn; sửa giá không cần `commercial.write`; sửa
phiên bản đã đóng; quyết phiên bản đã đóng; override sai loại chứng từ được nhận; ô bắt
buộc thiếu không được nêu; trường không khai báo được nhận; override của tenant nạp cho
mọi người; bước được cho một cổng đọc bản nháp; web: giá ẩn vẽ ra; web: bản nháp đã đóng
sửa được. 15/15 đỏ.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_document_drafts.py` (xuyên tenant
và workspace cho bản nháp và quyết định, override theo tenant, phiên bản và quyết định duy
nhất do database, id bản nháp không phải chứng từ của hồ sơ, `origin`/`draft_id` khớp),
`dw_platform/tests/integration/test_privileges.py::test_drafts_decisions_and_template_overrides_are_append_only`,
`test_case_documents.py` (CHECK `doc_type` có `bod_submission`), `test_rls_coverage.py` trên
ba bảng mới, migration chạy thật (upgrade rồi downgrade).

Chưa làm, ghi lại: PDF (LibreOffice, để adapter docgen); sửa bảng (dòng hàng, tiêu chí)
trên web, hiện chỉ sửa trường vô hướng; duyệt bản nháp thành chứng từ (ticket 05).
