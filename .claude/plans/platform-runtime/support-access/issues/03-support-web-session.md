# 03 — Web: phiên quyền hỗ trợ, trang "Quyền hỗ trợ của tôi", chỗ cắm băng

Status: ready-for-agent
Blocked by: 01, 02, .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md, .claude/plans/web-ui/antd-shell/issues/06-app-feedback.md, .claude/plans/web-ui/antd-shell/issues/09-region-state-and-offline.md
Area: platform-runtime

## Mục tiêu

Nhân viên hỗ trợ thấy các quyền được giao, mở một quyền, và từ đó mọi request mang mã quyền
cho tới khi thoát hay quyền kết thúc. Shell và API client là code chung, nên phần này nằm ở
nền tảng; chữ của băng, menu và trang chặn trong ngữ cảnh hỗ trợ thuộc context.

## Việc cần làm

1. `apps/web/lib/support/session.ts`: quyền đang dùng (id, mã, tenant, workspace, loại tài
   nguyên, id tài nguyên, khóa bộ scope, hạn) trong `sessionStorage` (mọi đọc, ghi bọc
   `try/catch`), xóa khi "Thoát quyền hỗ trợ" hoặc khi API trả `support_grant_ended`.
2. API client (`apps/web/lib/session.ts`): có quyền đang dùng thì gửi `X-DW-Support-Grant`,
   và `X-Tenant-Id`, `X-Workspace-Id` lấy từ quyền, không từ workspace đang chọn.
3. `NavItem` thêm `supportStaffOnly`; bộ lọc menu đọc `is_support_staff` của bootstrap.
   Người là nhân viên hỗ trợ và không có membership nào thì trang đầu là `/support`.
4. Shell có chỗ cắm băng dưới header (cùng chỗ băng mất mạng của antd-shell ticket 09); một
   context cắm băng của nó vào đó khi `useSupportGrant()` có quyền đang dùng.
5. Registry trang đích theo khuôn `apps/web/lib/approvals/registry.ts`: context đăng ký
   `supportLanding(resource_type, scope_set_key, resource_id) -> href`; không có mục khớp thì
   về `/`.
6. Trang `apps/web/app/support/page.tsx` "Quyền hỗ trợ của tôi": h1, phụ đề "Dữ liệu của khách
   chỉ mở được qua quyền hỗ trợ do khách cấp. Mỗi quyền có phạm vi, chế độ và hạn; mọi thao
   tác ghi nhật ký với mã quyền."; bảng từ 992px: Khách · Workspace · Phạm vi · chế độ · Hết
   hạn (giờ Việt Nam qua `lib/dates.ts`) kèm "còn {h} giờ" (vàng dưới 4 giờ; "Đã hết hạn" hoặc
   "Khách đã thu hồi" đỏ) · Trạng thái · nút "Mở" (khóa khi đã kết thúc, lý do "Quyền đã hết
   hạn hoặc bị thu hồi."); dưới 992px bốn cột gọn; trống "Chưa có quyền hỗ trợ nào" · "Khi
   khách cấp quyền, quyền hiện ở đây kèm phạm vi và hạn."; trạng thái vùng bằng
   `RegionState`. Dữ liệu `GET /support/my-grants`. `support_mfa_required` → trang chặn "Cần
   xác thực hai lớp để dùng quyền hỗ trợ". "Mở" ghi quyền vào phiên rồi tới trang đích của
   registry.
7. Một hook `useSupportGrant()` (quyền đang dùng hoặc `null`) để context ẩn thao tác trong
   ngữ cảnh hỗ trợ.

## Tiêu chí chấp nhận

- [ ] Vitest: client gửi `X-DW-Support-Grant` và tenant, workspace của quyền khi có quyền
      đang dùng, không gửi khi không có; nhận `support_grant_ended` thì phiên bị xóa;
      `sessionStorage` ném lỗi thì trang vẫn chạy, không có quyền đang dùng.
- [ ] Vitest: registry trang đích trả `href` của mục khớp, `/` khi không khớp.
- [ ] Playwright (stack local, một context test đăng ký trang đích trong fixture): nhân viên
      có token dev hai lớp thấy quyền ở `/support`, bấm "Mở" tới trang đích, request kế tiếp
      có header; token không có yếu tố thứ hai → trang chặn; 320px, 992px, 1440px, sáng và
      tối; project `tz-los-angeles` của antd-shell ticket 05 vẫn ghi giờ Việt Nam.
- [ ] Mục menu có `supportStaffOnly` chỉ hiện với nhân viên hỗ trợ; gỡ nhánh lọc → test đỏ.
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm build` xanh; `reviewing-deployment-security` trên
      header mới ở client và trang `/support`.

## Nguồn

- `apps/web/lib/session.ts:146-147` (header tenant, workspace hôm nay);
  `apps/web/lib/nav/types.ts` (`operatorOnly` là tiền lệ); `apps/web/lib/approvals/registry.ts`.
- Ticket 01 (`support_access`), 02 (`GET /support/my-grants`, `is_support_staff`, mã lỗi).
- `spec.md` của lát này: Mục tiêu 3.

## Comments

- 8/10/2026: chưa làm. Đã có điều ticket này chờ: antd ở mọi trang, `RegionState`
  (`@dw/ui`), thông báo qua `App.useApp()`, `lib/dates.ts` theo giờ Việt Nam,
  `GET /support/my-grants` và `is_support_staff` (ticket 02). Còn nguyên bước
  1–7: phiên quyền trong `sessionStorage`, header `X-DW-Support-Grant` trong
  `clientOptions`, `supportStaffOnly`, chỗ cắm băng, registry trang đích, trang
  `/support`, `useSupportGrant()`. Lưu ý từ ticket 02: lần đăng nhập đầu sau khi
  cài OTP, token chỉ có `["pwd"]`, nên trang `/support` phải hiện trang chặn
  `support_mfa_required` kèm lời "đăng nhập lại".
