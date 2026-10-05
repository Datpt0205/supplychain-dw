---
status: Proposed
date: 2026-10-05
source:
    - ../../apps/api/src/dw_api/settings.py # is_deployed 241, validate_for_profile 256-299, cors_origins 64
    - ../../apps/api/src/dw_api/main.py # CORS 93-107, docs ẩn 78-86, dev routes 150-163
    - ../../infra/compose/docker-compose.yml # keycloak KC_HOSTNAME, web build args 563-570, NEXT_PUBLIC_API_BASE_URL dòng 574
    - ../../infra/docker/web.Dockerfile # dòng 30-38
---

# E13. Host trên ba tên máy riêng cho web, API và đăng nhập, lấy từ env; TLS bằng overlay Caddy

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
