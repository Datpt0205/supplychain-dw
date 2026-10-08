# 01 — `anyScope` và một hàm hiện mục menu

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Một mục menu hiện cho người có một trong nhiều scope, và luật hiện mục có đúng một chủ cho
cả menu lẫn trang đầu (spec, Hiện trạng).

## Việc cần làm

1. `apps/web/lib/nav/types.ts`: `NavItem` thêm `anyScope?: string[]` (doc comment: điều hướng,
   không phải phân quyền; API vẫn kiểm).
2. `apps/web/lib/nav/visibility.ts`: `isNavItemVisible(item, { isPlatformOperator, hasScope,
roles })` gồm `operatorOnly`, `scope`, `anyScope`, `roles`. Danh sách rỗng ở `anyScope` là
   lỗi cấu hình: hàm trả `false` (đóng khi sai, failure-modes #7).
3. `apps/web/components/app-frame.tsx` và `apps/web/app/page.tsx` gọi hàm đó, bỏ hai bộ lọc
   viết tay.

## Tiêu chí chấp nhận

- [x] Vitest cho `isNavItemVisible`: mục có `anyScope` hiện với người có một scope trong danh
      sách, ẩn với người không có scope nào; có cả `scope` và `anyScope` thì cần cả hai;
      `anyScope: []` → ẩn; `operatorOnly`, `roles` giữ hành vi cũ. Gỡ nhánh `anyScope` → test
      đỏ.
- [x] `rg "item.scope \|\| hasScope" apps/web` ra 0 chỗ ngoài `visibility.ts`.
- [x] Menu và trang đầu không đổi với registry hiện có (Playwright hoặc vitest của
      `app-frame`).
- [x] `pnpm lint`, `pnpm typecheck`, `pnpm build` xanh.

## Nguồn

- `apps/web/lib/nav/types.ts`, `registry.ts`; `apps/web/components/app-frame.tsx:42-50`;
  `apps/web/app/page.tsx:24-30`.

## Comments

- 8/10/2026 (nhánh `feat/antd-everywhere`): `NavItem.anyScope`, một hàm
  `isNavItemVisible` ở `apps/web/lib/nav/visibility.ts`; `app-frame.tsx` và
  `app/page.tsx` cùng gọi nó, không còn bộ lọc viết tay
  (`rg "item.scope \|\| hasScope" apps/web` ra 0). `anyScope: []` trả `false`.
  Vitest `lib/nav/__tests__/visibility.test.ts` 4 ca. Mutation: gỡ nhánh
  `anyScope` thì 3/4 ca đỏ; trả lại thì xanh. Menu và trang đầu với registry
  hiện có không đổi: `home-page.test.tsx` và `app-frame-gate.test.tsx` xanh.
