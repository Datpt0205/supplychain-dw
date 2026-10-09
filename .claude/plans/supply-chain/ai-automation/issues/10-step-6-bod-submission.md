# 10 — Bước 6: tờ trình BGĐ trên approval

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

BGĐ thấy một tờ trình AI soạn (sản phẩm, NCC, các vòng mẫu, kết luận biên bản, giá nếu người xem có scope, rủi ro, khoảng trống) ngay trên trang cấp mã duyệt.

## Việc cần làm

1. `DocumentType` mới `bod_submission` (CHECK, enum zod, test bằng nhau).
2. Prompt `draft_bod_submission@1.0.0`; mỗi câu dẫn một chứng từ hoặc dòng lịch sử; code kiểm
   dẫn chứng như bộ kiểm của brief.
3. `pass_sample` vẫn khởi động approval BGĐ hiện có; run chuẩn bị gắn tờ trình vào approval
   trước khi báo BGĐ; thiếu tờ trình không chặn duyệt (ghi "chưa có tờ trình").
4. Tin Zalo vẫn không mang mã và không giá.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Câu dẫn chứng từ của hồ sơ khác bị loại.
- [ ] Người không có `commercial.read` không thấy giá trong tờ trình (bản dựng theo người xem hoặc không giá).
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Slide 4, 8 PoC; ADR 0016 sửa đổi S2; ADR 0014.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
