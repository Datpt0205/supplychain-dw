# 07 — Tin gửi NCC: AI soạn, người gửi (E18)

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Mọi tin gửi NCC là bản nháp AI soạn; người sao chép, gửi từ hộp thư của mình và bấm "Đã gửi"; nhắc NCC được soạn khi follow-up phía NCC mở.

## Việc cần làm

1. **Migration** `supplier_messages` (hồ sơ, NCC, người nhận từ `supplier_contacts`, mục đích
   CHECK, tiêu đề, thân, chứng từ đính kèm đã duyệt, mẫu phiên bản, `sent_at`, `sent_by`),
   workspace RLS, chỉ thêm (gửi là một dòng sự kiện).
2. Mẫu `sample_request`, `supplier_reminder`, `supplier_confirmation`; prompt
   `draft_supplier_message@1.0.0` với skill `supplier_email_style`.
3. Follow-up phía NCC (nhắc cập nhật, quá hạn mẫu) → bản nháp nhắc cho PIC.
4. **Web:** khối "Tin gửi NCC": sao chép, `mailto:`, "Đã gửi"; Zalo báo PIC có bản nháp,
   không mang nội dung có giá.
5. Không gửi từ hệ thống; không tool `external`.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; unit có xuyên workspace cho đọc và "Đã gửi"; xem Comments.)_
- [x] Không route, lane hay tool nào gửi email ra ngoài (test kiến trúc: không adapter SMTP/IMAP trong `dw_supply_chain`).
- [x] Tin không chứa số tài khoản; giá chỉ khi người soạn giữ `commercial.read`. _(Lane không giữ scope giá nên không thư nào có giá; đường người có scope giá tự xin thư chưa có.)_
- [x] "Đã gửi" ghi đúng người và phiên bản; follow-up nhắc đóng theo luật hiện có.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0029 (E18); ADR 0012 (bot không nhắn NCC); QE-17.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-07 (agent).** Đã làm (không Docker):

- Migration `1dc679326cb5` (hex của alembic, một head; dựng SQL offline, parse bằng `pglast`;
  **chưa chạy trên Postgres**): `supplier_messages` (hồ sơ PO hoặc phát triển, mục đích
  CHECK, trạng thái `drafted | refused | failed`, chỉ `drafted` có thân, người nhận là ảnh
  chụp, trích dẫn, số đoạn bị loại, mẫu và prompt, `content_sha256`, UNIQUE `source_key` theo
  workspace) và `supplier_message_sends` (một lần mỗi thư); workspace RLS FORCE, chỉ SELECT +
  INSERT.
- `domain.grounded_writing` (dùng chung cho 08–10): mô hình viết câu dẫn khóa bằng chứng;
  code giữ câu mà mọi khóa có trong bằng chứng của hồ sơ này, mọi con số có trong mục được
  dẫn (ngày tính theo từng phần), không có gì giống số tài khoản. `workflows.grounded_writing`:
  một lượt gọi có cấu trúc, bằng chứng là biến không tin cậy duy nhất.
- Prompt `draft_supplier_message@1.0.0` khai skill `supplier_email_style@1.1.0` (1.1.0 chỉ
  thêm `applies_to`); mẫu `supply_chain_supplier_messages@1.0.0` (tiêu đề, lời chào, hạn
  phản hồi, lời kết; tenant ghi đè qua `PolicyOverridePort`); policy chuẩn bị bước có thêm
  `supplier_messages` (nền tảng rỗng; override Elmich 1.1.0 bật `sample_request`,
  `supplier_reminder`, `supplier_confirmation`).
- Lane `supply_chain_supplier_messages` (worker, nhịp sweep follow-up, one-call gateway):
  nhắc NCC cho follow-up phía NCC đang mở (NCC im lặng; quá hạn `sample_collection`), đề nghị
  gửi mẫu khi vào bước 2, xác nhận sản phẩm khi vào bước 8, một lần mỗi nguồn; hồ sơ không
  thuộc workspace của lane bị từ chối trước lượt gọi; không plan thì không gọi; hết lượt thì
  dừng tenant; sai schema hay gọi hỏng vẫn ghi dòng nên không trả tiền lần hai. PIC được báo
  có bản nháp (mã hồ sơ và mục đích, không nội dung, không giá).
- Route `GET /{po,product}-cases/{id}/supplier-messages` (`document.read`), `POST
/supplier-messages/{id}/sent` (`document.write`, nhận `content_sha256` của văn bản đã sao
  chép; một lần). Web: `SupplierMessagesCard` trên hai trang hồ sơ (sao chép, `mailto:`, "Đã
  gửi" khóa kèm lý do).
- Tác vụ mô hình `draft.supplier_message` (`DRAFTING_TASKS`, route `profile_for`), cổng gom
  `task:draft.*`; routes policy 1.1.0 và dataset `supply_chain_preparation@1.1.0` (+8 ca:
  đúng có dấu, không dấu, chèn lệnh, xuyên tenant, xuyên workspace, thiếu bằng chứng, số bịa,
  sai schema). Test kiến trúc `test_no_outbound_mail.py`. ADR 0029 "Sửa đổi 2026-10-09".

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): grounding bỏ kiểm khóa lạ; giữ câu
không dẫn; bỏ kiểm số; bỏ kiểm số tài khoản; nhận số của mục không được dẫn (sống sót lần
đầu, ca `msg-number-fabricated` thêm đoạn dẫn sai mục rồi đỏ); drafter tin hồ sơ tenant /
workspace khác; gọi lại nguồn đã soạn; gọi khi không plan; sai schema không để dòng; lane
chạy tiếp sau hết lượt; mọi follow-up là phía NCC; thông báo mang thân thư; "Đã gửi" bỏ kiểm
văn bản; nhận thư không thân; không cần scope ghi; liệt kê bỏ kiểm workspace; nền tảng soạn
cho mọi tenant; mẫu nhận placeholder giá. 18/18 đỏ.

`reviewing-feature-security`: (1) RLS FORCE hai bảng, đọc và "Đã gửi" ngoài workspace là
404 (unit), integration nợ; (2) scope kiểm ở handler, test gọi thẳng handler; (3) tầm mới của
AI: ghi bản nháp thư và thông báo, không gửi gì (test kiến trúc); (4) bằng chứng là dữ liệu
trong `<input>`, ca chèn lệnh, đầu ra mô hình qua schema rồi code giữ/loại, hỏng thì
`refused`; (5) audit cùng giao dịch với dòng; (6) dòng sinh theo nguồn, xóa theo hồ sơ
(cascade), plan và hạn mức kiểm trước lượt gọi.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_supplier_messages.py` (xuyên tenant
và workspace, UNIQUE nguồn và gửi, CHECK thân, `dw_app` không UPDATE),
`dw_platform/tests/integration/test_privileges.py::test_supplier_messages_are_append_only`,
`test_rls_coverage.py` trên hai bảng mới, migration chạy thật (upgrade rồi downgrade),
`SqlSupplierContactLookup` với tên chuẩn hóa thật.

Chưa làm, ghi lại: người có scope giá tự xin một thư (đường API gọi mô hình); thư đính kèm
chứng từ đã duyệt (cột có, AI-09 dùng cho phiếu chỉnh sửa); thư chốt NCC so với BM04 (AI-12).
