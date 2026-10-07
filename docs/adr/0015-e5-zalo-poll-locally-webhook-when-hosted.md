---
status: Accepted
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

## Sửa đổi 2026-10-07 (tạm, lát Z3; Đạt ủy quyền quyết các điểm mở)

Quyết định tạm của lead khi làm ticket 03. Chi tiết, test và mutation ở Comments của
`.claude/plans/supply-chain/zalo-channel/issues/03-zalo-webhook.md`.

1. **Secret trong header, không trong đường dẫn.** Route là `POST /api/v1/zalo/webhook`;
   `scripts/zalo_webhook.py set` gửi `secret_token` cho `setWebhook`, Zalo gửi lại trong
   header `X-Bot-Api-Secret-Token`, API so bằng `hmac.compare_digest` trước khi đọc thân.
   Lý do: secret trong đường dẫn nằm trong access log của uvicorn, proxy và CDN (cùng hình
   SEC-20 của bot token). Thiếu hoặc sai header: 403 `permission_denied` (câu trả lời của
   nền tảng khi thiếu bearer), không xếp hàng gì. Chế độ khác `webhook` hoặc secret chưa
   đặt: route không mount, 404. **Chưa đo** (failure-modes #4): Zalo có gửi header này
   không; nếu không, mọi lệnh gọi bị 403 và deployment ở lại `poll`, là cách sai an toàn.
   Ticket 07 đo.
2. **Xử lý ngoài đường request: API xếp hàng, worker xử lý.** API kiểm secret, cỡ (64 KB,
   kể cả thân không khai độ dài), schema (phong bì `message` hoặc `result.message`, lỗi 422
   không echo), ghi nguyên update vào `platform.channel_inbound_updates` (mặt phẳng danh
   tính, không RLS, `dw_app` chỉ SELECT, INSERT, DELETE) và trả 200. Lane worker
   `zalo_webhook_drain` (2 giây) lấy bằng một `DELETE ... RETURNING` rồi gọi đúng
   `ZaloInbound.handle` do `build_zalo_inbound` dựng, đối tượng lane poll dùng, không đổi.
   Thay cho "API dựng cùng đối tượng": lệnh chat cần model, runner của review graph và
   lệnh của context đăng ký ở composition root của worker; dựng lại ở API là composition
   thứ hai. Lấy là xác nhận (như `getUpdates`); update không ai lấy quá 1 ngày bị lane
   retention xóa.
3. **Khử trùng ở router, không ở cửa.** Cùng update tới hai lần thì xếp hai dòng; router
   claim `message_id` trong `channel_inbound_messages`, nên lệnh chạy một lần. `/start`
   phát lại không liên kết lại vì mã dùng một lần (ADR 0012).
4. **Một bot, một người đọc.** API và worker đọc cùng `ZALO_UPDATES_MODE`: `poll` thì
   worker poll và API không có route; `webhook` thì API có route và worker chỉ drain.
   Compose truyền biến cho cả hai. `zalo_webhook.py set` từ chối khi chế độ khác `webhook`.
   Secret webhook chỉ API giữ (compose không còn đưa cho worker).
