# 01 — `docker-compose.host.yml` với Caddy, kiểm cấu hình deployed, `docs/deploy/host.md`

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md, .claude/plans/supply-chain/personal-settings/issues/01-settings-page-and-login.md
Area: supply-chain

## Mục tiêu

Stack chạy trên ba tên máy lấy từ env, có TLS, và từ chối khởi động khi cấu hình
deployed sai (spec, Mục tiêu). Ứng viên đưa ngược.

## Việc cần làm

1. `infra/compose/docker-compose.host.yml`: dịch vụ Caddy (ảnh ghim phiên bản) nghe 80,
   443; `Caddyfile` trong `infra/caddy/` với ba site `{$DW_WEB_HOST}`, `{$DW_API_HOST}`,
   `{$DW_AUTH_HOST}` chuyển tới `web:3000`, `api:8000`, `keycloak:8080`; header bảo mật
   cơ bản; không mở cổng dịch vụ nào khác ra ngoài.
2. Overlay đặt `KC_HOSTNAME=https://${DW_AUTH_HOST}`, `KC_PROXY_HEADERS=xforwarded`,
   `DW_API_OIDC_ISSUER_URL`, `DW_API_CORS_ORIGINS=["https://${DW_WEB_HOST}"]`,
   `DW_API_PUBLIC_BASE_URL=https://${DW_API_HOST}`, `DW_PUBLIC_WEB_URL`, và build args
   web `NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_KEYCLOAK_URL`,
   `NEXT_PUBLIC_AUTH_MODE=oidc`.
3. Runtime của web đã đọc `${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000}` và api đã
   nhận `DW_API_CORS_ORIGINS` (mặc định `[]`) từ base compose (5/10/2026). Còn lại: bỏ
   `API_INTERNAL_BASE_URL` không ai đọc.
4. **Realm theo env** là bước 1 của `personal-settings/issues/01` (U), mà H bị chặn
   bởi: `infra/keycloak/dw-realm.json:23-35` hôm nay còn ghi cứng `redirectUris`,
   `webOrigins`, `post.logout.redirect.uris` về `http://localhost:3000` (và sót
   `sales-dev.dxrank.vn:23000`). H chỉ truyền `DW_PUBLIC_WEB_URL` vào dịch vụ
   `keycloak` trong overlay và kiểm kết quả (tiêu chí dưới).
5. `validate_for_profile` (`settings.py:256-299`): khi `is_deployed`, issuer, mọi CORS
   origin, `DW_API_PUBLIC_BASE_URL` phải là `https://`.
6. Thêm `DW_WEB_HOST`, `DW_API_HOST`, `DW_AUTH_HOST` vào `.env.example` (đọc bởi overlay).
7. Runbook `docs/deploy/host.md`: DNS ba bản ghi; biến env; build image web theo tên
   miền; lần đầu import realm; sửa realm đã có trong trang quản trị (thêm redirect URI);
   đăng ký webhook Zalo (`scripts/zalo_webhook.py set`) và chuyển `ZALO_UPDATES_MODE`;
   kiểm tra sau khi lên (đăng nhập, `/health`, CORS, `iss`).

## Tiêu chí chấp nhận

- [ ] Test settings: profile deployed với issuer `http://`, CORS `http://` hoặc base URL
      `http://` từ chối khởi động; profile `local` không đòi. Mutation: bỏ kiểm CORS
      `https` thì test đỏ (ghi vào Comments).
- [ ] `docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml config`
      ra cấu hình không chứa tên miền nào ngoài biến env, và api có `DW_API_CORS_ORIGINS`.
- [ ] Chạy thử ở máy cá nhân với Caddy `tls internal`, không sửa file hosts: lệnh
      `curl --resolve` tới ba tên máy trả `/health` của api, trang web, và discovery OIDC có
      `issuer` là `https://<auth>`; một preflight CORS từ origin web được nhận, từ origin
      khác bị từ chối (ghi lệnh và kết quả vào Comments).
- [ ] Realm import với `DW_PUBLIC_WEB_URL=https://app.example.test`: client `dw-web`
      có đúng redirect, web origin và post-logout của URL đó, không còn
      `localhost`/`sales-dev` (đọc qua admin API của Keycloak cục bộ, ghi vào Comments).
- [ ] `reviewing-deployment-security` chạy trên diff; kết quả trong Comments.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 5 (compose, overlay, `web.Dockerfile:30-36`), mục 6
  (startup checks `settings.py:256-299`, CORS `main.py:93-107`, OIDC, TLS và proxy).
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 3, "Hosting (slice H)".

## Comments
