# 03 — Webhook Zalo và `scripts/zalo_webhook.py`

Status: ready-for-agent
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

- [ ] Test API: secret sai 404; secret chưa đặt 404; chế độ `poll` 404 kể cả secret
      đúng; thân 65 KB 413; JSON sai schema 422 và thân lỗi không chứa đầu vào; payload
      đúng với `/start <token>` hợp lệ thì liên kết (dùng lại test của ticket 01).
- [ ] Test settings: deployed + `webhook` + secret 16 ký tự từ chối khởi động; base URL
      `http://` từ chối; profile `local` thì không đòi.
- [ ] Mutation: thay `compare_digest` bằng `==` không làm test nào đỏ là chấp nhận được
      (khác biệt là timing); bỏ nhánh "chế độ khác webhook thì 404" thì test đỏ (ghi
      vào Comments).
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 4 (không chữ ký, secret trong đường dẫn, không giới hạn
  thân), 8 điểm 4 (Cloudflare 403 với user agent "Java"), 9.
- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` gap 11.
- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 6, "Zalo".

## Comments
