# 01 — `docker-compose.host.yml` với Caddy, kiểm cấu hình deployed, `docs/deploy/host.md`

Status: resolved
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

- [x] Test settings: profile deployed với issuer `http://`, CORS `http://` hoặc base URL
      `http://` từ chối khởi động; profile `local` không đòi. Mutation: bỏ kiểm CORS
      `https` thì test đỏ (ghi vào Comments).
- [x] `docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml config`
      ra cấu hình không chứa tên miền nào ngoài biến env, và api có `DW_API_CORS_ORIGINS`.
- [x] Chạy thử ở máy cá nhân với Caddy `tls internal`, không sửa file hosts: lệnh
      `curl --resolve` tới ba tên máy trả `/health` của api, trang web, và discovery OIDC có
      `issuer` là `https://<auth>`; một preflight CORS từ origin web được nhận, từ origin
      khác bị từ chối (ghi lệnh và kết quả vào Comments).
- [x] Realm import với `DW_PUBLIC_WEB_URL=https://app.example.test`: client `dw-web`
      có đúng redirect, web origin và post-logout của URL đó, không còn
      `localhost`/`sales-dev` (đọc qua admin API của Keycloak cục bộ, ghi vào Comments).
- [x] `reviewing-deployment-security` chạy trên diff; kết quả trong Comments.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 5 (compose, overlay, `web.Dockerfile:30-36`), mục 6
  (startup checks `settings.py:256-299`, CORS `main.py:93-107`, OIDC, TLS và proxy).
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 3, "Hosting (slice H)".

## Comments

- 2026-10-07, lead (Đạt ủy quyền quyết các điểm mở; quyết định tạm ghi ở ADR 0023, sửa đổi 2026-10-07).
    - **Code:** `infra/compose/docker-compose.host.yml`, `infra/caddy/Caddyfile`,
      `validate_for_profile` (issuer, mọi CORS origin, `DW_API_PUBLIC_BASE_URL` phải
      `https://` khi `is_deployed`; kiểm https riêng của webhook thành thừa, bỏ),
      `dw-realm.json` theo env, `.env.example` (ba tên máy, mạng proxy; bỏ dòng chết
      `# DW_API_HOST=0.0.0.0` vì trùng tên với biến mới), bỏ `API_INTERNAL_BASE_URL`,
      runbook `docs/deploy/host.md`, README.
    - **Bước 4 không phải của U:** realm vẫn ghi cứng `localhost:3200`/`127.0.0.1:3200`
      lúc bắt đầu, nên làm ở đây. Placeholder `${DW_PUBLIC_WEB_URL:http://localhost:3200}`
      chạy trên Keycloak 26.7.2: import với `DW_PUBLIC_WEB_URL=https://app.example.test`
      cho `['https://app.example.test/*'] ['https://app.example.test'] https://app.example.test/*`
      (admin API qua cổng loopback); import không có biến (start-dev, container riêng)
      cho `['http://localhost:3200/*'] ['http://localhost:3200'] http://localhost:3200/*`.
      Không còn `localhost`/`sales-dev` khi có biến.
    - **Test settings:** `test_a_deployed_profile_refuses_a_url_that_is_not_https`
      (uat, production × issuer http, base URL http/rỗng, CORS http, một http lẫn trong
      https) và `test_local_does_not_ask_for_https`. Mutation: bỏ CORS khỏi danh sách kiểm
      (`*((... ) for origin in self.cors_origins)` → `*()`) thì 4 test đỏ (2 profile × 2 ca
      CORS); trả lại thì xanh.
    - **`config`:** `-f docker-compose.yml` cộng {không, prod, uat, host, prod+host,
      uat+host} × profile {full, infra}: 12/12 `config -q` OK; dev overlay OK. Thiếu
      `DW_AUTH_HOST`: `required variable DW_AUTH_HOST is missing a value`. Cấu hình gộp
      prod+host: tên miền duy nhất là `*.example.test` từ env; api có
      `DW_API_CORS_ORIGINS: '["https://app.example.test"]'`; chỉ Caddy mở cổng ngoài
      loopback; Caddy ở `dw-ingress` + `dw-proxy`, không ở `dw-internal`/`dw-edge`.
      Còn sót, ngoài phạm vi: build arg `NEXT_PUBLIC_CHAT_BASE_URL=http://localhost:8100`
      không ai trong `apps/web` đọc.
    - **Chạy thử** (project `dwhost`, base + host, profile `local`, image có sẵn
      `--no-build`, `DW_CADDY_LOCAL_CERTS=local_certs`, `curl --ssl-no-revoke --resolve
<host>:443:127.0.0.1 --cacert root.crt`; đã `down -v`, không còn container):
        - Lần đầu Caddy chết "Address already in use": api lấy mất `.2` trước. Sửa bằng
          `ip_range` cấp động `.8/29`, Caddy cố định `.2`.
        - `https://api…/api/v1/health` 200, có HSTS, `nosniff`, `Referrer-Policy`,
          `X-Frame-Options: DENY`, không `Server`/`Via`; `/metrics` 404, `/` 404;
          `/api/openapi.json` 200 vì profile `local` (deployed thì ẩn trong app).
        - `https://app…/` 200 cùng header; `POST https://app…/api/v1/zalo/webhook` 404
          (webhook chỉ trên host API).
        - Discovery: `issuer` = `https://auth.example.test/realms/dw`. `/admin/`,
          `/admin/realms`, `/realms/master/...`, `/health/ready`, `/metrics`, `/` trên host
          auth: 404; `login-status-iframe.html` 200.
        - CORS preflight `Origin: https://app.example.test`: 200 với
          `Access-Control-Allow-Origin: https://app.example.test`; `Origin: https://evil.example`:
          400 `Disallowed CORS origin`.
        - `http://api…` → 308 sang https.
        - IP thật: log uvicorn ghi `172.22.0.1:0` (gateway của `dw-ingress`, không phải
          `172.30.250.2` của Caddy; cổng `:0` là dấu XFF được dùng). Gửi
          `X-Forwarded-For: 6.6.6.6` vẫn ghi `172.22.0.1`: Caddy thay header, không giả được.
          Trên Docker Desktop mọi người gọi mang IP gateway; runbook ghi chạy Linux.
        - Trang quản trị qua cổng loopback: console có `authUrl` `http://localhost:28686`;
          chưa thử đăng nhập console bằng trình duyệt.
    - **reviewing-deployment-security:** §1 không có bề mặt dev mới; `local_certs` trên
      deployment làm trình duyệt từ chối (hỏng lộ ra, không mở), docs ẩn theo `is_deployed`,
      `/metrics` và trang quản trị Keycloak bị chặn ở biên. §2 header ở biên như trên; CSP
      chưa đặt (cần đo với Next.js/antd, ghi ở ADR). §3 overlay không thêm secret; ba tên
      máy `:?`; secret webhook trong header, Caddy không ghi access log. §4 CORS đúng một
      origin từ env, đo cả nhận và từ chối. §5 đi ra mới duy nhất là ACME của Caddy
      (CA mặc định, không do người gọi quyết); Caddy không chạm `dw-internal`. §6
      `caddy:2.11.7-alpine@sha256:d8542f48…`: trivy 0.74.0 (image docker, trivy không cài
      trên máy) `--severity HIGH,CRITICAL`: alpine 3.23.6 0, `usr/bin/caddy` 0. (2.11.1 có
      nhiều CVE stdlib Go, vì vậy chọn 2.11.7.)
    - **Còn mở, ghi ở runbook:** `scripts/backup_postgres.sh` chỉ dump `dw`, database
      `keycloak` cần dòng cron `PG_DB=keycloak`; `scripts/deploy.sh` chưa biết overlay
      host; override SLA/đóng gói của Elmich trên máy chủ chỉ qua `PUT` với token lấy từ
      trình duyệt (chưa có màn hình); Zalo gửi `X-Bot-Api-Secret-Token` hay không vẫn chưa
      đo (ZL).
    - **Cổng:** `make lint` (lần đầu đỏ: SIM102 ở settings, prettier ở runbook; sửa rồi
      xanh), `typecheck`, `test-unit` (2952 passed, 3 skipped), `test-architecture`,
      `test-contract` (5 passed), `release-manifest-check`, `test-hooks` (76/0) xanh.
