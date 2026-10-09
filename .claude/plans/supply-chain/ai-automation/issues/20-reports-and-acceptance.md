# 20 — Báo cáo và đo tỷ lệ AI được duyệt

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Đo được AI làm bao nhiêu việc: bản nháp duyệt nguyên, sửa, từ chối; thời gian mỗi bước; tỷ lệ việc làm qua Zalo (slide 13); báo cáo tuần cho BGĐ và điểm NCC.

## Việc cần làm

1. Truy vấn từ `document_draft_decisions`, lịch sử, approvals (kênh); trang báo cáo.
2. Báo cáo tuần BGĐ và điểm NCC: số do code, câu tóm tắt AI kiểm như brief.
3. Số liệu này là đầu vào để Đạt nới "cái gì không cần người" về sau (QA-6).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [ ] Mỗi con số dẫn về hồ sơ; câu tóm tắt có số lạ bị bỏ.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Slide 9, 13 PoC; S8 (brief).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
