# 07 — Tin gửi NCC: AI soạn, người gửi (E18)

Status: ready-for-agent
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

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Không route, lane hay tool nào gửi email ra ngoài (test kiến trúc: không adapter SMTP/IMAP trong `dw_supply_chain`).
- [ ] Tin không chứa số tài khoản; giá chỉ khi người soạn giữ `commercial.read`.
- [ ] "Đã gửi" ghi đúng người và phiên bản; follow-up nhắc đóng theo luật hiện có.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- ADR 0029 (E18); ADR 0012 (bot không nhắn NCC); QE-17.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
