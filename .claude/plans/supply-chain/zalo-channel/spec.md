# Kênh Zalo: liên kết, gửi tin qua hộp thư kênh, webhook (lát Z1, Z2, Z3)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Phần chung, ứng viên đưa ngược. Code nằm trong `dw_connectors`, `dw_platform`,
`apps/api/src/dw_api/routes/v1/`, `apps/worker`; không import `dw_supply_chain`, không
có chữ Elmich ([ADR 0011](../../../../docs/adr/0011-e1-product-repo-builds-here-generic-pieces-stay-in-platform-packages.md)).
Quyết định: [ADR 0012](../../../../docs/adr/0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)
(Bot Platform, token dùng một lần, một bot mỗi deployment),
[ADR 0013](../../../../docs/adr/0013-e3-provider-neutral-channel-delivery-outbox.md)
(hộp thư kênh), [ADR 0014](../../../../docs/adr/0014-e4-decisions-only-on-the-web.md)
(quyết trên web), [ADR 0015](../../../../docs/adr/0015-e5-zalo-poll-locally-webhook-when-hosted.md)
(poll hay webhook).

## Mục tiêu

Một nhân viên liên kết Zalo của mình một lần; từ đó mọi thông báo trong ứng dụng gửi
tới họ cũng tới Zalo, có trạng thái, thử lại và audit; khi có host, bot nhận tin qua
webhook thay cho poll.

## Hiện trạng (kiểm trong code ngày 5/10/2026, `main` `bf553f4`)

- Có, đã test, chưa ai gọi: `ZaloBotClient` (`dw_connectors/adapters/zalo_bot.py:18-79`),
  token HMAC và `handle_update` (`zalo_link.py:25-47, 73-99`), `SqlZaloLink`
  (`dw_platform/adapters/persistence/zalo_link_repo.py:27-86`), `ChatSenderPort`
  (`dw_connectors/ports.py:27-41`).
- `packages/typescript/api-client/src/client.ts:84-97, 829-842` gọi
  `/api/v1/zalo/{status,connect,disconnect}` mà API không có route nào: trả 404.
- Compose truyền `ZALO_*` cho api và worker (`docker-compose.yml:362-369, 541-545`);
  settings không đọc.
- Thông báo chỉ vào hộp thư trong ứng dụng; `SqlNotificationRepository.deliver`
  (`notifications.py:101-131`) không có caller ở code chạy thật.

## Trong phạm vi

Ticket 01–03.

## Ngoài phạm vi

- Duyệt trong Zalo (ADR 0014). Email (cùng port, sau). Bot riêng mỗi tenant.
- Chọn sự kiện nào đi Zalo: mặc định mọi thông báo của người đã liên kết; Elmich trả
  lời QE-17 thì lọc theo loại ở phía nguồn thông báo.

## Tiêu chí xong

Ticket 01–03 `resolved` (agent); ticket 04 `resolved` khi một người dùng thật liên kết
Zalo ở máy cá nhân bằng poll và nhận một thông báo follow-up trên Zalo (ghi vào
Comments của ticket 04).

## Danh sách ticket

| #   | Ticket                                                                         | Status          | Blocked by                                                                                                                              |
| --- | ------------------------------------------------------------------------------ | --------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Liên kết Zalo: token một lần, route, lane poll](issues/01-zalo-link.md)       | ready-for-agent | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md |
| 02  | [Hộp thư kênh `channel_deliveries` và lane gửi](issues/02-channel-delivery.md) | ready-for-agent | .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md                                                                          |
| 03  | [Webhook Zalo và script đăng ký](issues/03-zalo-webhook.md)                    | ready-for-agent | .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md                                                                          |
| 04  | [Chạy thật với bot và Zalo thật](issues/04-live-run.md)                        | ready-for-human | 01, 02, 03, .claude/plans/supply-chain/hosting/issues/02-live-domain.md                                                                 |
