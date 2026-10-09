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

## Sửa đổi 2026-10-09 (tạm, lát AI-07)

1. **Ai soạn, khi nào.** Lane `supply_chain_supplier_messages` (worker, nhịp của sweep
   follow-up) soạn một thư cho mỗi nguồn kích hoạt (`source_key`, UNIQUE theo workspace):
   follow-up phía NCC đang mở (NCC im lặng; quá hạn mốc `sample_collection`), hồ sơ vào
   bước 2 (đề nghị gửi mẫu), hồ sơ vào bước 8 (xác nhận sản phẩm). Mục đích nào được soạn
   là trường `supplier_messages` của policy `supply_chain_step_preparation` của tenant
   (nền tảng: không mục đích nào; Elmich: cả ba).
2. **Mô hình viết thân, code viết khung.** Tiêu đề, lời chào, dòng hạn phản hồi (ngày do
   code tính) và lời kết lấy từ mẫu có phiên bản `supply_chain_supplier_messages@1.0.0`
   (tenant ghi đè qua `PolicyOverridePort`); mô hình chỉ viết đoạn thân, mỗi đoạn dẫn mục
   bằng chứng; code giữ đoạn mà mọi mục dẫn thuộc hồ sơ này, mọi con số có trong mục được
   dẫn, không có gì giống số tài khoản (`domain.grounded_writing`, dùng chung cho các bản
   nháp mô hình viết ở lát 08–10). Không đoạn nào qua được: thư `refused`, không thân,
   người tự viết.
3. **Không giá.** Lane không giữ `supply_chain.commercial.read`; bằng chứng mô hình thấy
   không có giá, và số không có trong bằng chứng bị loại. Thư soạn theo yêu cầu của một
   người có scope giá là việc của lát sau (chưa có đường đó).
4. **Người nhận** là ảnh chụp lúc soạn (tên, email người liên hệ mới nhất của NCC tìm theo
   tên chuẩn hóa trong workspace), không FK tới danh bạ có phiên bản.
5. **"Đã gửi"** (`supply_chain.document.write`) là một dòng `supplier_message_sends`, một
   lần mỗi thư, ghi người bấm và `content_sha256` của đúng văn bản đã sao chép; thư không
   thân không bấm được. "Đã gửi" không đóng follow-up: follow-up đóng theo luật của sweep.
6. **Lỗi không gọi lại mãi.** Câu trả lời sai schema (`refused`) hay gọi hỏng sau các lần
   thử của gateway (`failed`) vẫn ghi một dòng, nên lane không trả tiền lần hai cho cùng
   nguồn; hết lượt trong ngày thì không ghi gì và tenant dừng lượt đó.
