---
status: Accepted (Đạt, 2026-10-09)
date: 2026-10-09
source:
    - ../../../../../docs/adr/0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md # bot chỉ nhắn người đã nhắn trước
    - 0025-e14-ai-prepares-a-step-a-person-approves-the-move.md
---

# E18. Tin gửi NCC: AI soạn, người gửi; thư trả lời kéo vào hồ sơ

Mọi lần liên hệ NCC (xin mẫu, gửi phiếu, chốt sản phẩm, nhắc tiến độ, yêu cầu sửa) hôm nay
là một người tự viết. Bot Zalo không nhắn được NCC; repo không có kênh email.

**Quyết định:**

1. **AI soạn** tin gửi NCC thành bản nháp `supplier_messages` (người nhận từ danh mục NCC,
   tiêu đề, thân, chứng từ đính kèm đã duyệt) theo mẫu có phiên bản; nhắc NCC soạn khi một
   follow-up phía NCC mở.
2. **Người gửi:** nút sao chép (và `mailto:`) trên trang hồ sơ; người đó bấm "Đã gửi", hệ
   thống ghi ai, lúc nào, phiên bản nào. Không có hộp thư, không gửi ra ngoài từ hệ thống,
   không có tool `external`.
3. **Thư trả lời** được kéo vào hồ sơ (EML, MSG, PDF, ảnh) như chứng từ; lane trích xuất đọc
   (ADR 0021 sửa đổi 2026-10-09).
4. Đổi sang gửi tự động hay đọc hộp thư là một ADR mới.

## Hệ quả

- Ticket `ai-automation/issues/07`. Không thêm `ChatSenderPort` cho NCC.
