# `.env.example` đầy đủ (lát ENV)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Phần chung, ứng viên đưa ngược ([ADR 0011](../../../../docs/adr/0011-e1-product-repo-builds-here-generic-pieces-stay-in-platform-packages.md)).

## Mục tiêu

Một người mới clone repo chạy `make bootstrap` (chép `.env.example` thành `.env`) rồi điền secret; máy dev của chủ repo chép `.env` từ `codebase`. `.env.example` liệt kê đủ mọi biến mà settings đọc, và không biến nào được đọc mà
thiếu trong mẫu.

## Hiện trạng (kiểm trong code ngày 5/10/2026, `main` `bf553f4`)

- `.env.example` không có `ZALO_*`, `DW_API_CORS_ORIGINS`, `KC_HOSTNAME`,
  `DW_API_AUTO_PROVISION_MEMBERSHIP`, `DW_API_DEFAULT_TENANT_ID`/`_WORKSPACE_ID`/`_ROLE`;
  vẫn tả `apps/chat`, `DW_CHAT_*`, `NEXT_PUBLIC_CHAT_BASE_URL` không còn tồn tại.
- `apps/web/lib/auth/config.ts:17-18` và `infra/docker/web.Dockerfile:32` mặc định
  Keycloak `localhost:8080`; compose mở 8686, repo này mở 28686.
- `TELEGRAM_BOT_TOKEN` có trong `.env.example` và compose nhưng không ai đọc.

## Trong phạm vi

Ticket 01.

## Ngoài phạm vi

- Biến của hosting (`DW_WEB_HOST`, `DW_API_HOST`, `DW_AUTH_HOST`): `hosting/issues/01`.
- Đọc hoặc in giá trị của `.env` thật.

## Tiêu chí xong

Ticket 01 `resolved`; test đối chiếu settings với `.env.example` chạy trong CI.

## Danh sách ticket

| #   | Ticket                                                         | Status   | Blocked by                                                        |
| --- | -------------------------------------------------------------- | -------- | ----------------------------------------------------------------- |
| 01  | [`.env.example` đầy đủ](issues/01-env-example-and-init-env.md) | resolved | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md |
