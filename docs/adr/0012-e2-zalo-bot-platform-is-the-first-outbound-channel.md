---
status: Proposed
date: 2026-10-05
source:
    - ../../packages/python/dw_connectors/src/dw_connectors/ports.py # ChatSenderPort, dòng 27-41
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py # dòng 18-79
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # token 25-47, handle_update 73-99
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/zalo_link_repo.py # dòng 27-86
    - ../../db/migrations/sql/0001_platform_grants.sql # dòng 63-73
---

# E2. Zalo Bot Platform là kênh gửi ra đầu tiên; liên kết nằm ở `external_identities`, token dùng một lần, liên kết không bao giờ dựng AccessContext

Người dùng nội bộ của Elmich dùng Zalo, không dùng Slack. Kênh đầu tiên là **Zalo
Bot Platform** (`bot-api.zaloplatforms.com`), qua `ZaloBotClient` có sẵn trong
`dw_connectors`, thỏa `ChatSenderPort` (gửi một tin chữ). Người dùng tự liên kết:
bấm "Kết nối Zalo" ở trang cài đặt cá nhân, nhận dòng `/start <token>`, gửi cho bot.
Chat id Zalo được lưu ở `platform.external_identities` với `issuer = provider =
'zalo'` qua `SqlZaloLink`. `/stop` trong Zalo hoặc nút "Ngắt kết nối" gỡ liên kết.

**Một bot token cho mỗi deployment** (Đạt, 5/10/2026). Bot riêng cho từng tenant cần
một bảng liên kết có `tenant_id`; chưa làm.

## Những điều kiện đi kèm

1. **Liên kết không phải danh tính đăng nhập.** `external_identities` không có cột
   tenant và không có RLS. Đăng nhập an toàn chỉ vì `membership_lookup.py:77-80` so
   theo `issuer` của JWT đã kiểm, mà JWT không bao giờ mang `issuer='zalo'`. Ticket
   Z1 thêm test âm: một danh tính `zalo` không tìm ra membership nào. Webhook và lane
   poll không bao giờ gọi `access_context_factory`; quyết định không làm trong chat
   ([ADR 0014](0014-e4-decisions-only-on-the-web.md)).
2. **Token dùng một lần.** Token HMAC hiện có hạn 15 phút nhưng dùng lại được trong
   hạn: ai thấy token đều gắn được Zalo của mình vào người đó. Token thêm `jti`; bảng
   `platform.channel_link_nonces` (jti khóa chính, `user_id` FK `ON DELETE CASCADE`
   có index, `expires_at`, `used_at`) đánh dấu đã dùng trong cùng giao dịch với lúc
   liên kết. Dòng hết hạn được dọn bởi lane worker.
3. **Chạy bằng `dw_app`, không bằng `dw_provisioner`.** `0001_platform_grants.sql:63-73`
   không cấp `external_identities` cho provisioner, trái với docstring
   `zalo_link_repo.py:6-8`. Docstring được sửa theo grant, không ngược lại.
4. **Tên sản phẩm được tiêm.** `zalo_link.py:96` ghi cứng "Sale Intelligence" trong
   code nền tảng; câu trả lời của bot lấy tên từ cấu hình deployment.
5. **Cắt tin 1900 ký tự.** Zalo từ chối tin dài hơn 2000 ký tự mà không báo rõ (đo
   ở repo `dw` cũ, `zalo_bot.py:18-48`). Việc cắt là tính chất của kênh, nằm trong
   adapter.
6. **Token bot nằm trong URL.** Mọi lỗi httpx và log phải xóa token trước khi ghi
   (SEC-20 của sản phẩm đấu thầu).

## Phương án đã cân nhắc

- **Zalo OA cùng ZNS.** Gửi được tới người chưa nhắn trước, nhưng cần tài khoản doanh
  nghiệp xác minh, mẫu tin được duyệt và phí mỗi tin. Không cần cho nhân viên nội bộ.
- **Bản đồ Zalo id trong `.env` hoặc yaml** (repo `dw` cũ). Bác: chỉ dùng cho demo,
  và là bản sao thứ hai của danh sách người dùng.
- **Email trước.** Chưa có adapter và chưa có SMTP; để sau, cùng port.

## Hệ quả

- Bot Platform chỉ nhắn được người đã nhắn bot trước, nên nhà cung cấp không được
  nhắc qua kênh này. Elmich phải xác nhận kênh này chỉ cho nhân viên (QE-17).
- Một liên kết Zalo của một người dùng phục vụ mọi tenant họ là thành viên; tin gửi
  đi mang tên tenant và workspace để người nhận phân biệt.
- Không có nút bấm trong tin; mọi tin là chữ kèm liên kết về web.
