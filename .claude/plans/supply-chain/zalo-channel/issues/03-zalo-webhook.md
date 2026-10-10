# 03 — Webhook Zalo và `scripts/zalo_webhook.py`

Status: resolved
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md
Area: supply-chain

## Mục tiêu

Khi deployed có tên miền, bot nhận `/start`, `/stop` qua webhook thay cho poll, cùng
`handle_update` ([ADR 0015](../../../../../docs/adr/0015-e5-zalo-poll-locally-webhook-when-hosted.md)).
Ứng viên đưa ngược.

## Việc cần làm

1. `POST /api/v1/zalo/webhook/{secret}` trong `routes/v1/zalo.py`, chuyển từ `sales_dw`
   (`routes/v1/zalo.py:99-115`): so secret bằng `hmac.compare_digest` với
   `ZALO_WEBHOOK_SECRET`; secret chưa đặt hoặc `ZALO_UPDATES_MODE` khác `webhook` thì
   404; thân tối đa 64 KB (413); payload qua schema Pydantic, lỗi 422 không echo đầu
   vào; trả 200 nhanh rồi xử lý.
2. `validate_for_profile`: khi `is_deployed` và chế độ `webhook`, đòi secret từ 32 ký
   tự và `DW_API_PUBLIC_BASE_URL` bắt đầu bằng `https://`.
3. `scripts/zalo_webhook.py set|delete|info`: gọi `set_webhook`, `delete_webhook`,
   `get_webhook_info` của `ZaloBotClient` với URL dựng từ `DW_API_PUBLIC_BASE_URL`;
   không in secret hay token.
4. Thêm `ZALO_WEBHOOK_SECRET`, `DW_API_PUBLIC_BASE_URL` vào settings và
   `.env.example`.

## Tiêu chí chấp nhận

- [x] Test API: secret sai ~~404~~ 403 (secret ở header, ADR 0015 sửa đổi Z3 mục 1);
      secret chưa đặt 404; chế độ `poll` 404 kể cả secret
      đúng; thân 65 KB 413; JSON sai schema 422 và thân lỗi không chứa đầu vào; payload
      đúng với `/start <token>` hợp lệ thì liên kết (dùng lại test của ticket 01).
- [x] Test settings: deployed + `webhook` + secret 16 ký tự từ chối khởi động; base URL
      `http://` từ chối; profile `local` thì không đòi.
- [x] Mutation: thay `compare_digest` bằng `==` không làm test nào đỏ là chấp nhận được
      (khác biệt là timing); bỏ nhánh "chế độ khác webhook thì 404" thì test đỏ (ghi
      vào Comments).
- [x] `make ci` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 4 (không chữ ký, secret trong đường dẫn, không giới hạn
  thân), 8 điểm 4 (Cloudflare 403 với user agent "Java"), 9.
- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` gap 11.
- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 6, "Zalo".

## Comments

**2026-10-07, lead (Đạt ủy quyền quyết điểm mở; ADR 0015 sửa đổi Z3).**

Quyết định tạm (safest reading):

- **Secret ở header `X-Bot-Api-Secret-Token`, không ở đường dẫn.** Route
  `POST /api/v1/zalo/webhook` (không `{secret}`); `set_webhook(url, secret_token=)`.
  Secret trong đường dẫn vào access log của uvicorn/proxy/CDN. Thiếu hoặc sai: 403
  `permission_denied` (như thiếu bearer ở mọi route), kiểm trước khi đọc thân, không xếp
  hàng. Chế độ `poll` hoặc secret rỗng: route không mount (404), và handler hỏi lại.
  Chưa đo Zalo có gửi header không: ticket 07 đo; không gửi thì 403 hết, ở lại `poll`.
- **"Trả 200 nhanh rồi xử lý" = xếp hàng ở Postgres, worker xử lý.** Bảng mặt phẳng danh
  tính `platform.channel_inbound_updates` (`8728e2fac660`, không RLS, `dw_app` SELECT,
  INSERT, DELETE; CHECK channel, payload là object); lane `zalo_webhook_drain` (2 s, lô 20)
  lấy bằng `DELETE ... RETURNING` và gọi `ZaloInbound.handle` của `build_zalo_inbound`
  không đổi. Dòng không ai lấy quá 1 ngày (`INBOUND_UPDATE_RETENTION`) bị lane
  `channel_inbound_messages_retention` xóa (chứa chữ của người gửi).
- **Khử trùng:** ở router, `channel_inbound_messages` (Z4a); cửa không khử trùng.
- **Một bot một người đọc:** cùng `ZALO_UPDATES_MODE` cho api và worker (compose truyền
  cho api); poll lane và drain lane loại trừ nhau trong `build_registry`. Secret webhook
  bỏ khỏi env của worker.
- **Schema:** phong bì `result.message` (khi có `result`) hoặc `message` là object; trường
  lạ giữ nguyên; update không có message (sự kiện khác) là 422 cho tới khi ticket 07 thấy
  một sự kiện như vậy. Thân giới hạn theo byte thực đọc, không chỉ `Content-Length`.
- **`scripts/zalo_webhook.py set|delete|info`** đọc `ApiSettings` (một chủ của tên biến),
  `set` từ chối khi chế độ khác `webhook`, thiếu secret, base URL không `https://`; chỉ in
  URL.

Test: `apps/api/tests/unit/test_zalo_webhook_endpoint.py` (403 sáu biến thể, 404 poll và
secret rỗng, 413 có và không khai độ dài, 64 KB đúng thì nhận, 422 sáu dạng không echo,
CORS preflight không cho header secret, settings deployed), `test_zalo_webhook_script.py`,
`dw_connectors` `test_zalo_bot.py` (set_webhook gửi secret, lỗi không lộ),
`apps/worker` `test_zalo_webhook_consumer.py` (đúng một lane mỗi chế độ),
`test_zalo_webhook_db.py` (`/start` qua webhook liên kết; cùng update hai lần chạy lệnh
một lần; hai drain cùng lúc không chia nhau update), `test_privileges.py`.

Mutation (đỏ trừ khi ghi khác): bỏ `mode == "webhook"` trong `zalo_webhook_enabled` →
`test_poll_mode_has_no_webhook_even_with_the_right_secret` đỏ; `_admitted` trả True →
đỏ; `compare_digest` → `==` → xanh (chấp nhận, khác biệt là timing); bỏ kiểm cỡ khi đọc
luồng → chỉ test thân chunked đỏ (test 65 KB có `Content-Length` vẫn xanh, nên thêm
test chunked); bỏ nhánh "không có message" → đỏ; bỏ kiểm secret ≥32 → đỏ; bỏ kiểm
`https://` → đỏ; đăng ký drain cả ở chế độ poll → `test_exactly_one_reader_per_mode[poll]`
đỏ.

Rà deployment security: route không vào OpenAPI (`include_in_schema=False`), mount chỉ khi
bật ở mọi profile; lỗi 403/413/422 chỉ có taxonomy; compose mặc định rỗng = tắt (fail
closed); `.env.example` để trống; header secret không có trong `_CORS_HEADERS` (test
preflight); không fetch ra ngoài từ route, URL `setWebhook` do operator đặt; không image
mới. Rate limit 240/phút theo IP vẫn áp cho webhook: sau reverse proxy mọi request chung
một IP (có sẵn, không riêng ticket này); đủ cho quy mô Elmich, ghi để H/H2 xem.
