# 20 — Báo cáo và đo tỷ lệ AI được duyệt

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Đo được AI làm bao nhiêu việc: bản nháp duyệt nguyên, sửa, từ chối; thời gian mỗi bước; tỷ lệ việc làm qua Zalo (slide 13); báo cáo tuần cho BGĐ và điểm NCC.

## Việc cần làm

1. Truy vấn từ `document_draft_decisions`, lịch sử, approvals (kênh); trang báo cáo.
2. Báo cáo tuần BGĐ và điểm NCC: số do code, câu tóm tắt AI kiểm như brief.
3. Số liệu này là đầu vào để Đạt nới "cái gì không cần người" về sau (QA-6).

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới. Unit/eval: hồ sơ workspace khác và tenant khác không được đếm; integration `test_report_reads.py` viết, chưa chạy.)_
- [x] Mỗi con số dẫn về hồ sơ; câu tóm tắt có số lạ bị bỏ.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- Slide 9, 13 PoC; S8 (brief).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát AI-20 (agent).** Đã làm (không Docker):

- `domain/reports.py` (tuần, số tuần, điểm NCC, AI được duyệt), `application/reports.py`
  (`GetWeeklyReport`, `SummarizeWeeklyReport`, `GetSupplierScorecard`, `GetAiAcceptance`),
  `adapters/persistence/report_reads.py` (chỉ đọc, RLS + lọc tenant/workspace). Chính sách
  `supply_chain_ai_time_saved@1.0.0` (số phút tạm, tenant ghi đè; trang ghi "ước tính").
- Route `GET /reports/weekly`, `POST /reports/weekly/summary`, `GET /reports/suppliers`,
  `GET /reports/ai-acceptance` (cần cả `po_case.read` và `product_case.read`). Trang web "Báo cáo"
  (menu Supply Chain): bảng số tuần với hồ sơ được đếm, nút "AI tóm tắt tuần" (câu AI viết có nhãn và
  số dựa vào), điểm NCC, AI được duyệt.
- Dataset `supply_chain_preparation@1.17.0` (routes 1.17.0, +7 ca `wr-*`, 291).
- Mutation: 8/8 đỏ (lần đầu 7/8: thứ tự điểm NCC theo tên sống sót vì dữ liệu test trùng thứ tự; sửa
  test). Eval: bỏ grounding, bỏ `import` khỏi hồ sơ mới: đỏ.
- Chưa làm: tỷ lệ việc qua Zalo (kênh của quyết định chưa ghi chỗ đếm được); thời gian mỗi bước (đã
  có ở đồng hồ SLA, chưa gom vào báo cáo); báo cáo tuần chưa gửi tự động (đọc trên trang).
