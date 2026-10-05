# Đăng nhập cổng và trang cài đặt cá nhân (lát U)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Phần chung, ứng viên đưa ngược. Quyết định: [ADR 0022](../../../../docs/adr/0022-e12-portal-login-through-keycloak-oidc.md)
(Keycloak OIDC, quản trị viên tạo người dùng, dev chỉ ở máy cá nhân),
[ADR 0012](../../../../docs/adr/0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)
(liên kết Zalo do người dùng tự làm).

## Mục tiêu

Người dùng Elmich đăng nhập bằng tài khoản quản trị viên tạo, mở "Cài đặt cá nhân" từ
chip phiên, thấy hồ sơ và membership của mình, và liên kết hoặc ngắt Zalo.

## Hiện trạng (kiểm trong code ngày 5/10/2026, `main` `bf553f4`)

- Không có trang cài đặt cá nhân. `app/admin/settings/page.tsx:28-40` là cài đặt
  **tenant**, cần `platform.tenant.settings.write`. `components/session-chip.tsx` chỉ
  có liên kết audit và đăng xuất.
- `GET /me` (`routes/v1/me.py:27-38`) chỉ đọc. `/auth/bootstrap` trả membership.
- Realm `dw`: `redirectUris`, `webOrigins` ghi cứng `localhost:3000`, `127.0.0.1:3000`
  và một tên máy của dự án cũ; `post.logout.redirect.uris` chỉ localhost; không
  `loginTheme`; `verifyEmail: true` mà không SMTP.
- Thẻ Zalo của `sales_dw` (`components/sales/zalo-connect-card.tsx`, 157 dòng) dùng
  shadcn `Button` và lucide.

## Trong phạm vi

Ticket 01.

## Ngoài phạm vi

- Đổi mật khẩu, tên, email trong ứng dụng: dẫn sang trang tài khoản của Keycloak.
- Theme đăng nhập riêng của Elmich (QO-4).

## Tiêu chí xong

Ticket 01 `resolved`; đăng nhập thật bằng Keycloak cục bộ (cổng 28686) với một người
dùng tạo trong trang quản trị, rồi liên kết Zalo từ trang mới.

## Danh sách ticket

| #   | Ticket                                                                      | Status          | Blocked by                                                                                                                                                                                                                                                 |
| --- | --------------------------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Realm từ env, trang cài đặt cá nhân](issues/01-settings-page-and-login.md) | ready-for-agent | .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md, .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md, .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md |
