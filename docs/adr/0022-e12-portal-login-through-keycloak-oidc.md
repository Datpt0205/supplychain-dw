---
status: Accepted
date: 2026-10-05
source:
    - ../../apps/api/src/dw_api/settings.py # AuthMode dòng 30, auth_mode 74, OIDC 76-86, auto_provision 100-105
    - ../../apps/api/src/dw_api/bootstrap/identity.py # dòng 17-27
    - ../../apps/api/src/dw_api/routes/v1/auth.py # /auth/bootstrap 44-74
    - ../../apps/web/lib/auth/config.ts # dòng 11-23
    - ../../infra/keycloak/dw-realm.json
---

# E12. Đăng nhập cổng qua Keycloak OIDC; quản trị viên tạo người dùng; chế độ dev chỉ ở máy cá nhân

Nền tảng đã có đủ đường đăng nhập: API kiểm JWT RS256 của Keycloak theo JWKS, với
`issuer` và `audience` bắt buộc (`KeycloakTokenVerifier`); web dùng `keycloak-js`
với PKCE S256, token chỉ giữ trong bộ nhớ; lần đầu đăng nhập `/auth/bootstrap` tạo
người dùng và `external_identity`. Quyết định là dùng đúng đường đó cho cổng Elmich,
không dựng đường thứ hai.

- **Một realm cho mỗi deployment, giữ tên `dw` và client `dw-web`.** Tên là của nền
  tảng, không mang tên khách. Web đọc realm, client và URL Keycloak từ
  `NEXT_PUBLIC_KEYCLOAK_*`; API đọc `DW_API_OIDC_ISSUER_URL`.
- **URL của cổng lấy từ env.** `redirectUris`, `webOrigins` và
  `post.logout.redirect.uris` của `dw-web` hôm nay ghi cứng `localhost:3000`,
  `127.0.0.1:3000` và một tên máy của dự án cũ. Chúng chuyển sang placeholder env
  của file import realm. Keycloak có hỗ trợ placeholder khi import; phải chạy và nhìn
  trước khi dựa vào (failure-modes #4). File realm chỉ được import vào database
  Keycloak rỗng; realm đã có thì sửa ở trang quản trị, ghi trong runbook.
- **Không tự đăng ký.** `registrationAllowed: false` giữ nguyên. Quản trị viên tạo
  người dùng trong Keycloak, rồi gán membership và vai `sc_*` qua
  `/admin/workspaces` hoặc `POST /api/v1/admin/members`. `DW_API_AUTO_PROVISION_MEMBERSHIP`
  để `false`: người đăng nhập lần đầu không có membership cho tới khi được gán.
- **Quên mật khẩu và xác minh email cần SMTP** trong realm. Hôm nay `verifyEmail: true`
  mà realm không có `smtpServer`, nên email xác minh không đi được.
- **Chế độ dev chỉ ở máy cá nhân.** `DW_API_AUTH_MODE=dev` với `/dev-login` và roster
  seed, hoặc Keycloak cục bộ ở cổng 28686 với `scripts/keycloak_dev_users.py`. Route
  dev chỉ mount khi không `is_deployed`, và `validate_for_profile` từ chối chế độ dev
  khi deployed; cả hai đã có.
- **Giao diện trang đăng nhập:** theme `sales` được mount trong compose nhưng realm
  không đặt `loginTheme`, nên không bao giờ được dùng. Đặt `loginTheme` từ env; theme
  riêng của Elmich là điểm mở.

## Phương án đã cân nhắc

- **Realm hoặc client mang tên Elmich** (`elmich-portal`). Bác: tên khách đi vào cấu
  hình nền tảng và vào `iss` của token.
- **Đăng nhập bằng Zalo.** Bác: liên kết Zalo không phải danh tính
  ([ADR 0012](0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)).

## Hệ quả

- Mặc định `KEYCLOAK_URL` của web là `localhost:8080` (`config.ts:17-18`,
  `web.Dockerfile:32`) trong khi compose mở 8686 (28686 ở repo này): sửa ở ticket ENV.
- Trang cài đặt cá nhân (ticket U) là nơi người dùng thấy hồ sơ, membership và liên
  kết Zalo của mình; đổi mật khẩu dẫn sang trang tài khoản của Keycloak.
