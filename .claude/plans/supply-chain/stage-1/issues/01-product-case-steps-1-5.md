# 01 — Hồ sơ phát triển sản phẩm: bước 1–5 (đề xuất, lấy mẫu, test, chỉnh sửa)

Status: ready-for-agent
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

- [ ] Unit domain: mọi chuyển hợp lệ; mọi chuyển sai trạng thái bị từ chối; hành động
      \* thiếu lý do bị từ chối; vòng chỉnh sửa tăng `sample_round`; ngắt rồi `resume`
      trả về trạng thái trước.
- [ ] PIC: body gửi kèm `pic_user_id` bị 422; PIC là người tạo.
- [ ] `pass_sample` thiếu biên bản, hoặc biên bản của hồ sơ khác cùng tenant: 409 nêu
      loại chứng từ thiếu.
- [ ] Duty: `sc_operator` không `pass_sample` được; `sc_rnd` không `propose` được (403).
- [ ] **Test âm RLS** cho bốn bảng mới: tenant B không đọc, không ghi; workspace khác
      không đọc; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] **Test âm route:** `GET`, `POST actions`, `documents` trên hồ sơ của tenant khác
      hoặc workspace khác trả 404.
- [ ] Trùng `proposal_code` trong tenant: 409 theo tên ràng buộc; cùng mã ở tenant khác
      được.
- [ ] Mutation: bỏ kiểm chứng từ ở `pass_sample` thì test đỏ; bỏ đóng dấu PIC thì test
      đỏ (ghi vào Comments).
- [ ] Vitest trang danh sách và chi tiết (trạng thái rỗng, lỗi, nút khóa có lý do).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 1–5; mục 5 điểm 4.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3 ("Entities", bảng trạng thái hàng 1–7, "PIC rule").
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4 ("New tables", "State machine", "PIC").
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, "The dw_supply_chain package" (mẫu `POCase`, `apply_action`).

## Comments

- Giả định: vòng chỉnh sửa là `revision_requested` → `sample_testing` (QE-07); không
  giới hạn số vòng.
