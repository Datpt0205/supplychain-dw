# 01 — Liên kết sang hộp duyệt của context trên `/approvals`

Status: resolved
Blocked by: .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md
Area: platform-runtime

## Mục tiêu

Approval có tiền tố mà một context đã khai hộp riêng thì `/approvals` hiện một liên kết sang
hộp đó thay cho nút quyết (spec, Mục tiêu). Một cửa quyết trên web cho mỗi approval.

## Việc cần làm

1. `apps/web/lib/approvals/types.ts`: `ApprovalHost` thêm
   `inbox?: { label: string; href: (approval: Approval) => string }`; `client` thành
   `client?: () => ApiClient`. Doc comment: liên kết là điều hướng, không phải phân quyền.
2. `apps/web/lib/approvals/registry.ts`:
    - một hàm thuần `findHost(hosts, approvalType)` mà cả `approvalClient` lẫn
      `approvalInbox` gọi (một phép so tiền tố, không hai);
    - `approvalClient` thiếu `client` thì trả `apiClient()`;
    - `approvalInbox(approval)` trả `{ label, href: inbox.href(approval) }` hoặc `null`.
      `href` không bắt đầu bằng một dấu `/` (đường dẫn trong ứng dụng) thì coi là lỗi cấu
      hình: không hiện liên kết, cũng không hiện nút quyết, và hiện "This request is decided
      elsewhere." (đóng khi sai, failure-modes #7).
3. `apps/web/app/approvals/page.tsx`: approval `pending` có `approvalInbox` thì hiện nút liên
   kết (antd `Button` với `href`) mang `label`, không ô ghi chú, không "Approve", "Reject",
   kể cả với người có `approvals.decide`. Approval khác giữ nguyên hành vi. Đổi các thành
   phần shadcn của trang sang antd (CLAUDE.md, Web UI), không đổi chữ hay luồng.

## Tiêu chí chấp nhận

- [x] Vitest `registry.test.ts` cho `findHost`, `approvalInbox` với danh sách host giả: khớp
      tiền tố thì trả liên kết; không khớp thì `null`; host không có `inbox` thì `null` và
      `approvalClient` vẫn chọn đúng client; host thiếu `client` thì `apiClient()`; `href`
      tuyệt đối (`https://…`) thì không liên kết.
- [x] Vitest `approvals-page.test.tsx` (mock registry có một host với tiền tố `ctx.` và
      `inbox`): người có `approvals.decide` thấy approval `ctx.x` với liên kết đúng `label`,
      `href` và không có nút "Approve", "Reject" hay ô ghi chú; approval `tool.y` vẫn có hai
      nút. Gỡ nhánh `approvalInbox` trong trang thì test đỏ (ghi vào Comments).
- [x] `HOSTS` rỗng: trang không đổi với dữ liệu hiện có (test trên).
- [x] `rg "startsWith" apps/web/lib/approvals` ra đúng một chỗ.
- [x] `pnpm lint`, `pnpm typecheck`, `pnpm build` xanh.

## Nguồn

- `apps/web/lib/approvals/types.ts`, `registry.ts:15-22`; `apps/web/app/approvals/page.tsx`;
  `packages/typescript/contracts/src/runs.ts:5-16`.

## Comments

- 8/10/2026 (nhánh `feat/antd-everywhere`, Đạt giao quyết tạm): 03 và 05 của
  `antd-shell` chưa xong nhưng không chặn thật: trang đã sang antd ở commit
  "antd everywhere", `lib/dates.ts` đã tính theo giờ Việt Nam, và vitest đã
  chạy được với `vitest.setup.ts`.
- `ApprovalHost.client` không bắt buộc, thêm `inbox`. Registry có ba hàm thuần
  nhận danh sách host (`findHost`, `clientFor`, `inboxFor`) và hai hàm gắn với
  `HOSTS` (`approvalClient`, `approvalInbox`). Một phép so tiền tố
  (`rg "startsWith" apps/web/lib/approvals` ra đúng một chỗ, ngoài test);
  kiểm "đường dẫn trong ứng dụng" bằng regex `^/(?!/)`, nên `//host` cũng bị từ
  chối, không chỉ `https://`.
- Quyết tạm: câu cho `href` sai cấu hình là tiếng Việt, "Yêu cầu này được quyết
  ở nơi khác." (cả shell đã sang tiếng Việt cùng ngày). `approvalInbox` trả
  `{ kind: "link" | "misconfigured" }` thay cho `{ label, href } | null`, để
  trang phân biệt "không có hộp riêng" với "hộp riêng cấu hình sai" mà không
  đọc lại `href`.
- Test: `lib/approvals/__tests__/registry.test.ts` (6 ca: khớp, không khớp,
  host không `inbox`, client của host và mặc định, `https://` và `//`);
  `app/approvals/__tests__/approvals-inbox.test.tsx` (người có
  `approvals.decide` thấy liên kết đúng `label`, `href`, thẻ `ctx.x` không có ô
  nhận xét hay nút quyết, thẻ `tool.y` vẫn đủ). `HOSTS` rỗng: các ca cũ của
  `approvals-page.test.tsx` không đổi và xanh.
- Mutation: bỏ điều kiện `inbox === null` ở nhánh quyết trên trang thì test
  trang đỏ; trả lại thì xanh.
