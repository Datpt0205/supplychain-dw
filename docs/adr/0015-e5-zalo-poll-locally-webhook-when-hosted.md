---
status: Proposed
date: 2026-10-05
source:
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py # get_updates, set_webhook
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # parse_update 60-70
    - ../../infra/compose/docker-compose.yml # ZALO_* dòng 362-369, 541-545
---

# E5. Nhận tin Zalo bằng poll khi chạy máy cá nhân, bằng webhook khi có host

Bot nhận `/start` và `/stop` theo một trong hai cách, chọn bằng
`ZALO_UPDATES_MODE=poll|webhook`. Hai cách loại trừ nhau trên một bot: `getUpdates`
chỉ chạy sau `deleteWebhook`. Cả hai gọi cùng `handle_update`, nên code ra trước,
đổi cách nhận khi có tên miền.

- **`poll`** (mặc định khi không deployed): worker đăng ký lane `zalo_link_poll`,
  chỉ gọi ra ngoài, không cần host công khai. `getUpdates` xác nhận ngay khi đọc:
  update xử lý lỗi thì mất, người dùng gửi lại `/start`. Lane chỉ đăng ký khi có cả
  bot token và link secret.
- **`webhook`**: API mount `POST /api/v1/zalo/webhook/{ZALO_WEBHOOK_SECRET}`. So
  secret bằng `hmac.compare_digest`; secret chưa đặt hoặc chế độ khác `webhook` thì
  trả 404. Thân tối đa 64 KB, kiểm bằng schema Pydantic, không echo đầu vào khi lỗi.
  `scripts/zalo_webhook.py set|delete|info` đăng ký webhook với
  `DW_API_PUBLIC_BASE_URL`.
- Zalo không ký request; secret trong đường dẫn chỉ chặn rác. Thứ thật sự bảo vệ
  việc liên kết là token HMAC dùng một lần ([ADR 0012](0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)).

## Ràng buộc khi deployed

- `validate_for_profile` từ chối khởi động khi `is_deployed`, chế độ `webhook` và
  secret ngắn hơn 32 ký tự hoặc `DW_API_PUBLIC_BASE_URL` không phải `https://`.
- CDN đứng trước API phải cho user agent "Java" của Zalo đi qua; Cloudflare từng trả
  403 (commit `b13b8f67` của `sales_dw`). Nếu không mở được, ở lại `poll`.

## Phương án đã cân nhắc

- **Chỉ webhook.** Bác: không chạy được trên máy cá nhân trước khi có tên miền.
- **Chỉ poll, cả khi deployed.** Dùng được, nhưng mỗi update mất khi worker lỗi đúng
  lúc đó, và nhiều worker cùng poll một bot thì tranh update. Giữ làm đường lui.

## Hệ quả

- Chỉ một worker chạy lane poll; số bản sao worker khác 1 thì phải tắt lane trên các
  bản còn lại.
