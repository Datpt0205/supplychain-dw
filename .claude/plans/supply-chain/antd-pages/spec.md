# Dựng lại các trang Supply Chain bằng antd (lát W)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Riêng của context. Đạt quyết ngày 5/10/2026: trang web chuyển nguyên trạng ở lát port,
dựng lại bằng antd ở lát này. `CLAUDE.md` (Web UI) và `.claude/rules/ui-quality.md` áp
dụng.

## Mục tiêu

Mọi trang dưới `apps/web/app/supply-chain/` và component dưới
`apps/web/components/supply-chain/` dùng antd và các mảnh chung của `@dw/ui`, không còn
thành phần shadcn hay lucide, không đổi dữ liệu hay luồng.

## Hiện trạng

- Sáu trang (danh sách và chi tiết Hồ sơ PO, attention queue, control tower, daily
  brief, follow-ups) và tám component (`brief-summary`, `case-query-answer`,
  `case-query-bar`, `case-state-badge`, `daily-brief`, `missing-update-badge`,
  `sla-status-badge`, `supplier-event-badge`), viết trước quyết định antd ngày
  28/9/2026, dùng `@dw/ui` và lucide.
- `CASE_STATE_LABEL` trong `case-state-badge.tsx` là chỗ duy nhất giữ nhãn trạng thái
  Hồ sơ PO.

## Trong phạm vi

Ticket 01. Trang mới của giai đoạn 1 đã viết bằng antd ở các ticket `stage-1`.

## Ngoài phạm vi

Thay đổi nội dung, luồng, API.

## Tiêu chí xong

Ticket 01 `resolved`; `rg "lucide-react|from \"@dw/ui\"" apps/web/app/supply-chain apps/web/components/supply-chain`
chỉ còn các mảnh chung `@dw/ui` mà `CLAUDE.md` cho phép.

## Danh sách ticket

| #   | Ticket                                                                 | Status   | Blocked by                                                                                                                                                                                                                                                                                                                                                                                            |
| --- | ---------------------------------------------------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Dựng lại trang và component](issues/01-rebuild-supply-chain-pages.md) | resolved | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md, .claude/plans/web-ui/antd-shell/issues/07-status-tones-and-tag.md, .claude/plans/web-ui/antd-shell/issues/08-page-header-and-cells.md, .claude/plans/web-ui/antd-shell/issues/09-region-state-and-offline.md |
