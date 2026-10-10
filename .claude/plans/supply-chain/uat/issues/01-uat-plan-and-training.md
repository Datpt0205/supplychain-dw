# UAT-01 — Kế hoạch UAT, hướng dẫn sử dụng, danh sách nghiệm thu

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Elmich có kịch bản thử từng bước 1–17 theo vai, hướng dẫn ngắn có ảnh, và danh sách nghiệm thu khớp với slide.

## Việc cần làm

1. `docs/products/elmich/uat-plan.md`: kịch bản theo vai, dữ liệu thử, tiêu chí đạt.
2. Hướng dẫn sử dụng tiếng Việt theo vai (web và Zalo), ảnh chụp từ Playwright.
3. Danh sách nghiệm thu: mỗi hứa hẹn của slide → ticket → cách kiểm.

## Tiêu chí chấp nhận

- [x] Mỗi bước 1–17 có ít nhất một kịch bản; mỗi hứa hẹn slide có một dòng nghiệm thu.

## Nguồn

- `poc-slides-brief.md` slide 10, 13.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10 (agent).** `docs/products/elmich/uat-plan.md` (vai, môi trường và dữ liệu thử, 23 kịch
bản bước 1–17 mỗi kịch bản có kiểm âm, 9 kịch bản chung: Zalo, trợ lý hồ sơ, bản tin, nhắc hạn, báo
cáo, nạp dữ liệu, tách nhiệm, chèn lệnh; tiêu chí đạt, ghi lỗi, lịch 5 ngày),
`docs/products/elmich/uat-acceptance.md` (mỗi điều slide 1–13 hứa và phần AI bước 11–17 → ticket →
mã kịch bản → ô kết quả, ô ký), `docs/products/elmich/huong-dan-su-dung.md` (hướng dẫn tiếng Việt
cho 11 vai, web và Zalo, theo đúng nhãn nút trên màn hình). Nợ: ảnh màn hình (Playwright, cần môi
trường chạy; không có Docker trên máy này); tỷ lệ việc qua Zalo của slide 13 chưa đo (AI-20).
