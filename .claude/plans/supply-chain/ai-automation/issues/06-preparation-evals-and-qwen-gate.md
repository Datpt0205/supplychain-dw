# 06 — Eval chuẩn bị và cổng Qwen

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md
Area: supply-chain

## Mục tiêu

Mọi trích xuất và bản nháp có dataset và grader; một tác vụ chỉ chạy trên Qwen khi qua cùng dataset.

## Việc cần làm

1. Dataset `evals/datasets/supply_chain_preparation@1.0.0` (tiếng Việt có dấu và không dấu),
   grader `supply_chain.<gate>` trong `dw_supply_chain.testing`, đăng ký ở
   `scripts/run_evals.py`. Ca bắt buộc mỗi tác vụ: đúng, chèn lệnh, xuyên tenant/workspace,
   thiếu bằng chứng, số bịa, mâu thuẫn, file không đọc được.
2. Mỗi ticket 08–18 thêm ca của nó vào dataset (phiên bản tăng).
3. Chạy dataset với profile `luna` và profile Qwen (3B, 9B) của `configs/models`; bảng kết
   quả trong Comments; policy `supply_chain_model_routes` chọn profile theo tác vụ, chỉ cho
   Qwen tác vụ đã qua ngưỡng.
4. Bổ sung ca cho `delay_impact_analysis` (Open của area file).

## Tiêu chí chấp nhận

- [ ] Mỗi ca an ninh đỏ khi bỏ guard (ghi lệnh đột biến và kết quả).
- [ ] Tác vụ chưa qua ngưỡng trên Qwen không thể chọn Qwen (policy từ chối khi nạp).
- [ ] Kết quả model thật ghi vào Comments, không chỉ mock.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `.claude/plans/supply-chain.md` Open: model accuracy unmeasured; stage-1 ticket 07.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments
