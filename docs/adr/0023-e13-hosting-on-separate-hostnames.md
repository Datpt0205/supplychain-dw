---
status: Accepted
date: 2026-10-05
source:
    - ../../apps/api/src/dw_api/settings.py # is_deployed 241, validate_for_profile 256-299, cors_origins 64
    - ../../apps/api/src/dw_api/main.py # CORS 93-107, docs ẩn 78-86, dev routes 150-163
    - ../../infra/compose/docker-compose.yml # keycloak KC_HOSTNAME, web build args 563-570, NEXT_PUBLIC_API_BASE_URL dòng 574
    - ../../infra/docker/web.Dockerfile # dòng 30-38
---

# E13. Host trên ba tên máy riêng cho web, API và đăng nhập, lấy từ env; TLS bằng overlay Caddy

> Phần chung đã đưa về platform (`2ebd50f`) thành ADR 0009 của platform ([`0009-hosting-on-three-hostnames-behind-caddy.md`](0009-hosting-on-three-hostnames-behind-caddy.md)); code platform ở đây trích ADR 0009, ADR này giữ phần của Elmich.

**Quyết định (Đạt chọn tên máy riêng, 5/10/2026):**

- Ba tên máy, mỗi cái một biến env: web (`DW_WEB_HOST`), API (`DW_API_HOST`), Keycloak
  (`DW_AUTH_HOST`). Không tên miền nào ghi trong code hay compose.
- Overlay `infra/compose/docker-compose.host.yml` thêm Caddy: nhận 80 và 443, tự lấy
  chứng chỉ, chuyển tới `web:3000`, `api:8000`, `keycloak:8080` với `X-Forwarded-*`.
  Mọi cổng khác vẫn chỉ mở trên `127.0.0.1`.
- `KC_HOSTNAME=https://${DW_AUTH_HOST}` để `iss` của token bằng
  `DW_API_OIDC_ISSUER_URL`; API vẫn lấy khóa trong mạng nội bộ qua
  `DW_API_OIDC_JWKS_URL`.
- `DW_API_CORS_ORIGINS` là đúng origin của web. Hôm nay compose gốc và overlay
  prod/uat không truyền biến này, nên prod và uat **từ chối khởi động**; chỉ overlay
  dev có.
- Biến `NEXT_PUBLIC_*` được đóng vào lúc build: mỗi deployment build image web của
  mình với URL API và URL Keycloak đúng. Bỏ giá trị runtime ghi cứng
  `NEXT_PUBLIC_API_BASE_URL: http://localhost:8000` (compose dòng 574).
- `validate_for_profile` thêm điều kiện khi `is_deployed`: issuer, mọi CORS origin và
  `DW_API_PUBLIC_BASE_URL` phải là `https://`. Cổng điều kiện là `is_deployed`, không
  bao giờ `profile == "production"`.
- Runbook `docs/deploy/host.md`: DNS, biến env, build, lần import realm đầu, sửa
  realm đã có, đăng ký webhook Zalo, kiểm tra sau khi lên.

## Phương án đã cân nhắc

- **Một tên máy, chia theo đường dẫn** (`/api`, `/auth`). Bác: Keycloak dưới đường dẫn
  con cần `KC_HTTP_RELATIVE_PATH` và cookie dùng chung origin với ứng dụng; tên máy
  riêng giữ cookie đăng nhập ở origin riêng.
- **nginx.** Dùng được; Caddy tự lấy và gia hạn chứng chỉ với cấu hình ngắn hơn.

## Hệ quả

- Đổi tên miền là build lại image web.
- Webhook Zalo ([ADR 0015](0015-e5-zalo-poll-locally-webhook-when-hosted.md)) chỉ bật
  được sau overlay này; CDN đứng trước phải cho user agent "Java" đi qua.
- Code của overlay ra trước; chạy thật khi có tên miền.

## Sửa đổi 2026-10-07 (tạm, lát H; Đạt ủy quyền quyết các điểm mở)

Quyết định tạm của lead khi làm ticket hosting/01. Lệnh, kết quả và mutation ở Comments
của `.claude/plans/supply-chain/hosting/issues/01-caddy-overlay-and-runbook.md`.

1. **Caddy `2.11.7-alpine`, ghim tag và digest**; trivy 0.74.0: 0 HIGH/CRITICAL. Chỉ ở
   hai mạng: `dw-ingress` (cổng công khai, ACME) và `dw-proxy` (internal, chỉ Caddy, web,
   api, Keycloak). Caddy không tới được Postgres, Qdrant, Valkey.
2. **Proxy tin được là một địa chỉ.** `dw-proxy` có subnet cố định; Caddy giữ một IP
   ngoài `ip_range` cấp động. API tin `X-Forwarded-*` chỉ từ IP đó (`FORWARDED_ALLOW_IPS`
   của uvicorn, mặc định của nó là loopback), Keycloak cũng vậy
   (`KC_PROXY_TRUSTED_ADDRESSES`). Caddy thay `X-Forwarded-For` bằng IP nó thấy, nên giới
   hạn theo IP của API thấy người gọi thật. CDN đứng trước cần `trusted_proxies`.
3. **Danh sách cho phép theo host, không phải chuyển cả host.** API: chỉ `/api/*`
   (`/metrics` không xác thực và dựa vào mạng nội bộ). Keycloak: chỉ `/realms/*`,
   `/resources/*`, `/robots.txt`, trừ realm `master`. Trang quản trị Keycloak qua đường hầm
   SSH tới cổng loopback, `KC_HOSTNAME_ADMIN=http://localhost:<cổng>`.
4. **`DW_API_PUBLIC_BASE_URL` phải `https://` ở mọi profile deployed**, không chỉ khi
   webhook (theo ticket); kiểm riêng cho webhook trong `validate_for_profile` thành thừa và
   được bỏ.
5. **Realm theo env làm ở đây** (bước 1 của U chưa làm): `${DW_PUBLIC_WEB_URL:http://localhost:3200}`
   trong `dw-realm.json`, đã chạy trên Keycloak 26.7.2 cả khi có và khi không có biến. Bỏ
   `127.0.0.1:3200` khỏi realm cục bộ.
6. **Header ở biên:** HSTS 1 năm `includeSubDomains` (không preload), `nosniff`,
   `Referrer-Policy`, `X-Frame-Options: DENY` trên web và API (không trên host đăng nhập:
   iframe trạng thái đăng nhập của keycloak-js), bỏ `Server`, `Via`, `X-Powered-By`. CSP
   chưa đặt: cần đo với Next.js và antd trước.
7. **Không access log ở Caddy**, để header secret của webhook Zalo không vào log; runbook
   ghi bộ lọc nếu bật.
