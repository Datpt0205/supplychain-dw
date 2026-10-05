# Host trên tên miền thật: overlay Caddy, kiểm cấu hình deployed, runbook (lát H)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Phần chung, ứng viên đưa ngược. Quyết định: [ADR 0023](../../../../docs/adr/0023-e13-hosting-on-separate-hostnames.md).
Code ra ngay; chạy thật khi có tên miền.

## Mục tiêu

Với ba tên máy trong env (web, API, đăng nhập), một lệnh compose dựng stack có TLS,
đăng nhập Keycloak đúng `iss`, CORS đúng origin, và API từ chối khởi động khi cấu hình
deployed thiếu hoặc sai.

## Hiện trạng (kiểm trong code ngày 5/10/2026, `main` `bf553f4`)

- Không có reverse proxy; mọi cổng chỉ mở trên `127.0.0.1`.
- Compose gốc và overlay prod/uat không truyền `DW_API_CORS_ORIGINS`, nên prod và uat từ
  chối khởi động (`settings.py:285`).
- `NEXT_PUBLIC_API_BASE_URL: http://localhost:8000` ghi cứng ở runtime
  (`docker-compose.yml:574`); `NEXT_PUBLIC_*` đóng vào lúc build
  (`docker-compose.yml:563-570`, `web.Dockerfile:30-38`).
- `infra/helm`, `infra/terraform` rỗng; không có runbook.

## Trong phạm vi

Ticket 01.

## Ngoài phạm vi

Helm, Terraform, CDN, sao lưu ra ngoài máy (đã có ở ops-hardening).

## Tiêu chí xong

Ticket 01 `resolved` phần code; phần chạy thật là ticket 02 (`ready-for-human`), đóng khi có tên miền.

## Danh sách ticket

| #   | Ticket                                                                          | Status          | Blocked by                                                                                                                                              |
| --- | ------------------------------------------------------------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Overlay Caddy, kiểm deployed, runbook](issues/01-caddy-overlay-and-runbook.md) | ready-for-agent | .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md, .claude/plans/supply-chain/personal-settings/issues/01-settings-page-and-login.md |
| 02  | [Chạy thật trên ba tên miền](issues/02-live-domain.md)                          | ready-for-human | .claude/plans/supply-chain/hosting/issues/01-caddy-overlay-and-runbook.md                                                                               |
