# Mục menu hiện khi người dùng có một trong nhiều scope

Status: resolved
Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Một context có trang mà hai nhóm người khác nhau
cùng mở được (vd người làm nghiệp vụ, và quản trị viên tổ chức chỉ có scope nền tảng). Hôm
nay `NavItem` chỉ nhận một `scope`, nên mục đó hoặc ẩn với một nhóm, hoặc phải khai hai
lần.

## Hiện trạng (đã kiểm trong code ngày 3/10/2026)

- `NavItem` (`apps/web/lib/nav/types.ts`) có `scope?: string`, `roles?: string[]`,
  `operatorOnly?: boolean`.
- Luật hiện mục viết hai lần, giống hệt nhau: `apps/web/components/app-frame.tsx:42-50` (menu)
  và `apps/web/app/page.tsx:24-30` (lối tắt ở trang đầu). Thêm một điều kiện vào một chỗ mà
  quên chỗ kia thì trang đầu mời người dùng tới mục menu đã ẩn (failure-modes #2).

## Trong phạm vi

- `NavItem` thêm `anyScope?: string[]`: hiện khi người dùng có ít nhất một scope trong danh
  sách; `scope` giữ nguyên nghĩa; khai cả hai thì phải thỏa cả hai.
- Một hàm `isNavItemVisible(item, viewer)` ở `apps/web/lib/nav/` mà cả `app-frame.tsx` lẫn
  `app/page.tsx` gọi.

## Ngoài phạm vi

- Ẩn mục theo dữ liệu (vd mục biến mất khi một việc đã xong): chưa ai cần ở nền tảng.
- Menu không phải phân quyền: route vẫn kiểm scope ở API.

## Tiêu chí xong

- Ticket 01 `resolved`; `pnpm lint`, `pnpm typecheck`, `pnpm build`, vitest của web xanh.

## Phụ thuộc

Không bị chặn bởi gì.

## Danh sách ticket

| #   | Ticket                                                            | Status   | Blocked by |
| --- | ----------------------------------------------------------------- | -------- | ---------- |
| 01  | [`anyScope` và một hàm hiện mục menu](issues/01-nav-any-scope.md) | resolved | —          |
