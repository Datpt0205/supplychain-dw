# 01 — Realm lấy URL cổng từ env; trang `/settings` bằng antd với thẻ Zalo

Status: resolved
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md, .claude/plans/supply-chain/env/issues/01-env-example-and-init-env.md, .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md
Area: supply-chain

## Mục tiêu

Đăng nhập cổng chạy với URL lấy từ env, và mỗi người có một trang cài đặt của mình
(spec, Mục tiêu). Ứng viên đưa ngược.

## Việc cần làm

1. **Realm** `infra/keycloak/dw-realm.json`: `redirectUris`, `webOrigins`,
   `post.logout.redirect.uris` của `dw-web` lấy từ placeholder env (URL web cục bộ và
   URL web deployed); bỏ tên máy của dự án cũ; `loginTheme` từ env; `smtpServer` từ
   env (`KC_SMTP_*`, thêm vào `.env.example`); `registrationAllowed: false` giữ nguyên.
   **Chạy Keycloak với file đã sửa và nhìn** rằng placeholder được thay (failure-modes
   #4); ghi phiên bản Keycloak và kết quả vào Comments. Nếu placeholder không chạy ở
   phiên bản đang ghim, dùng script sinh file realm từ mẫu lúc khởi động và ghi lại.
2. **Trang** `apps/web/app/settings/page.tsx` (antd, theo `.claude/rules/ui-quality.md`):
    - hồ sơ chỉ đọc từ `GET /me` (tên, email), kèm liên kết "Đổi mật khẩu" sang trang
      tài khoản Keycloak;
    - danh sách membership của chính người xem (tenant, workspace, vai) từ dữ liệu
      bootstrap có sẵn;
    - `ZaloConnectCard` viết lại bằng antd từ thẻ của `sales_dw`: chưa liên kết, đang
      chờ (hiện `/start <code>`, nút chép, giờ hết hạn theo `lib/dates.ts`, deep link
      nếu có), đã liên kết (nút "Ngắt kết nối" có xác nhận), chưa cấu hình (route trả
      404), lỗi. Mutation mang `Idempotency-Key`.
3. **Lối vào:** mục "Cài đặt cá nhân" trong `components/session-chip.tsx`. Trang mở
   cho mọi người đã đăng nhập, không đòi scope.
4. Không thêm thành phần shadcn nào; không sửa trang khác.

## Tiêu chí chấp nhận

- [ ] Vitest cho `ZaloConnectCard`: đủ năm trạng thái trên; bấm kết nối hai lần nhanh
      chỉ gửi một request; ngắt kết nối hỏi xác nhận, Cancel giữ nguyên.
- [ ] Vitest cho trang: chỉ hiện membership của người xem (fixture có hai người).
- [ ] Không có dữ liệu tenant nào ngoài membership của chính người xem trong response
      mà trang gọi (kiểm network trong test hoặc Playwright).
- [ ] Keycloak cục bộ import realm mới: đăng nhập từ `http://localhost:3000` chạy; một
      redirect URI không có trong env bị Keycloak từ chối (ghi vào Comments).
- [ ] Mọi điều khiển tới được bằng bàn phím, có tên tiếng Việt cho `getByRole`; trang
      đứng ở 320 px.
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm build`, vitest xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 1 (realm, `auth-context.tsx:184-215`,
  `app-frame.tsx:70-120`), mục 2 (không có trang cài đặt cá nhân), mục 6 "OIDC".
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 3 (luồng liên kết trên trang cài đặt), mục 10.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 3.

## Comments

- 2026-10-05, resolved: Keycloak OIDC login verified by a scripted Authorization Code + PKCE flow on http://localhost:3200 and by an adversarial reviewer; `/settings` (antd) shows profile, memberships and the Zalo card; `make ci` green (unit 1455 passed).
