# 09 — Bước 3–5: biên bản đánh giá, phiếu chỉnh sửa, kiểm vòng mẫu

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

R&D nhập kết quả đo theo danh sách tiêu chí; AI soạn biên bản, đối chiếu tiêu chuẩn của Category, soạn phiếu khi không đạt, và ở vòng sau kiểm từng mục của phiếu.

## Việc cần làm

1. Policy `supply_chain_sample_criteria@1.0.0` (tiêu chí theo Category, ngưỡng số; tenant
   ghi đè); skill `sample_evaluation`.
2. Form checklist của vòng mẫu (giá trị đo, ảnh); code so ngưỡng → phát hiện.
3. Bản nháp `sample_evaluation` (biên bản) và, khi có tiêu chí trượt, `sample_revision_request`
   (phiếu) từ tiêu chí trượt và các vòng trước; cùng bản nháp tin gửi NCC (ticket 07).
4. Vòng sau: mỗi mục phiếu cũ → đã sửa / chưa / không kiểm được.
5. Approval bước vật lý: ô kết quả Đạt / Cần chỉnh sửa / Hủy để trống, gợi ý bên cạnh.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Kết quả rỗng không duyệt được; gợi ý không điền sẵn.
- [ ] Ngưỡng của tenant A không áp cho B.
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` mục 3.2 hàng 3–5; ADR 0016.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
