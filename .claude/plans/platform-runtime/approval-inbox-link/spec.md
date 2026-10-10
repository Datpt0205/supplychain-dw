# Trang `/approvals` dẫn approval của một context sang hộp duyệt riêng của context đó

Area: platform-runtime · Nhánh: `main` (worktree `codebase-main`) · Viết: 3/10/2026

Lát nền tảng, trung tính với sản phẩm. Một bounded context có thể dựng hộp duyệt riêng cho
các approval của mình (bằng chứng, điều kiện quyết, khóa danh tính). Hôm nay trang
`/approvals` của nền tảng vẫn liệt kê các approval đó kèm nút quyết, nên người dùng có hai
cửa quyết cho cùng một việc, và cửa của nền tảng không hiện điều kiện mà context đã dựng.

## Mục tiêu

Một context khai một lần, ở registry của trang approvals, rằng approval có tiền tố của nó
được quyết ở hộp riêng. Với approval đó, `/approvals` hiện một liên kết sang hộp của context
thay cho ô ghi chú và hai nút quyết. Context không khai gì thì trang giữ nguyên.

## Hiện trạng (đã kiểm trong code ngày 3/10/2026)

- `ApprovalHost` (`apps/web/lib/approvals/types.ts`) có `prefix` và `client` (bắt buộc).
- `apps/web/lib/approvals/registry.ts:15` có `HOSTS: ApprovalHost[] = []`;
  `approvalClient(approvalType)` (dòng 17-22) chỉ chọn client theo tiền tố, mặc định
  `apiClient()`. Registry chưa nói gì về nơi quyết.
- `apps/web/app/approvals/page.tsx` vẽ ô ghi chú và hai nút "Approve", "Reject" cho mọi
  approval `pending` khi người xem có `approvals.decide`. Trang còn dùng thành phần shadcn/ui
  của `@dw/ui` (Card, Button, Input, Table, Badge).
- Approval (`packages/typescript/contracts/src/runs.ts:5-16`) không mang workspace; liên kết
  do context dựng từ chính approval (`id`, `run_id`, `approval_type`).

## Trong phạm vi

- `ApprovalHost` thêm `inbox?: { label: string; href: (approval: Approval) => string }`;
  `client` thành không bắt buộc (thiếu thì dùng `apiClient()`), vì context chạy trong API
  của nền tảng không cần client riêng.
- Registry thêm `approvalInbox(approval)`: trả `{ label, href }` của host khớp tiền tố, hoặc
  `null`. Registry vẫn là nơi duy nhất ánh xạ tiền tố.
- `/approvals`: approval `pending` có hộp riêng hiện nút liên kết `label` tới `href`, không có
  ô ghi chú, không có nút quyết, với mọi người xem. Bảng "Recent decisions" không đổi.
- Trang bị chạm nên đổi sang antd theo CLAUDE.md (Web UI): không thêm thành phần shadcn nào.

## Ngoài phạm vi

- Phân quyền. Ẩn nút không phải phân quyền: route quyết của nền tảng vẫn nhận quyết định của
  người có `approvals.decide`. Context nào cần chặn người quyết thì kiểm ở nơi run tiếp tục
  (node của chính nó), như hiện nay.
- Liệt kê approval theo workspace: `approval-audit-and-workspace/issues/02`.

## Tiêu chí xong

- Ticket 01 `resolved`; `pnpm lint`, `pnpm typecheck`, `pnpm build`, vitest của web xanh.

## Phụ thuộc

- `.claude/plans/web-ui/antd-shell/issues/03-lib-dates.md` (sửa cùng trang, tránh xung đột).
- `.claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md` (vitest, fixture).

## Danh sách ticket

| #   | Ticket                                                                                         | Status   | Blocked by                                                                                                           |
| --- | ---------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------- |
| 01  | [Liên kết sang hộp duyệt của context trên `/approvals`](issues/01-approval-host-inbox-link.md) | resolved | .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md |
