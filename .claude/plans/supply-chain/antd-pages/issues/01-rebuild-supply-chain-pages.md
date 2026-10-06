# 01 — Dựng lại sáu trang và tám component Supply Chain bằng antd

Status: ready-for-agent
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/web-ui/antd-shell/issues/03-lib-dates.md, .claude/plans/web-ui/antd-shell/issues/05-ui-test-harness.md, .claude/plans/web-ui/antd-shell/issues/07-status-tones-and-tag.md, .claude/plans/web-ui/antd-shell/issues/08-page-header-and-cells.md, .claude/plans/web-ui/antd-shell/issues/09-region-state-and-offline.md
Area: supply-chain

## Mục tiêu

Trang Supply Chain trông và hành xử như các trang khác của shell antd (spec, Mục tiêu).

## Việc cần làm

1. Mỗi trang một commit, theo thứ tự: danh sách Hồ sơ PO, chi tiết Hồ sơ PO, attention
   queue, follow-ups, daily brief, control tower.
2. Bảng dùng antd `Table` theo `ui-quality.md` mục 11 (`rowKey`, phân trang keyset với
   footer, ô định danh là `Link`, bộ lọc thành chip có "Xóa tất cả").
3. Huy hiệu trạng thái, SLA, cập nhật thiếu, sự kiện NCC dùng tag và tông trạng thái
   chung của `@dw/ui` (antd-shell 07); `CASE_STATE_LABEL` vẫn là chỗ duy nhất giữ nhãn.
4. Mỗi vùng tự tải có đủ trạng thái loading, rỗng (ba nghĩa), lỗi, 403, 404 qua thành
   phần trạng thái vùng chung (antd-shell 09).
5. Giờ qua `lib/dates.ts`, có "giờ Việt Nam".
6. Câu tóm tắt AI của brief giữ nhãn nguồn và nhóm được dẫn (`ui-quality.md` mục 7).
7. Xóa component cũ khi không còn ai import.

## Tiêu chí chấp nhận

- [ ] Test vitest có sẵn của từng trang vẫn xanh (sửa selector, không sửa ý); mỗi trang
      thêm test cho trạng thái rỗng và lỗi.
- [ ] Không còn `lucide-react` và thành phần shadcn trong hai thư mục (lệnh `rg` của
      spec).
- [ ] Playwright: mỗi trang đứng ở 320 px và ở hai phía mốc `lg`; mọi điều khiển tới
      được bằng `getByRole` với tên tiếng Việt.
- [ ] Không màu, cỡ chữ, bo góc nào ngoài theme (`ui-quality.md` mục 1).
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm build` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, "Web (14 files plus tests)".
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 "Web" (trang viết trước quyết định antd).
- `CLAUDE.md`, "Web UI".

## Comments

- 2026-10-06, Đạt: giao diện theo ngôn ngữ thiết kế E-HSDT v3
  (`docs/design/ehsdt/design_handoff_ehsdt_v3/`, chỉ có trên máy; README và
  `doi-chieu-codebase.md` là luật), sửa cho đúng quy trình Elmich. Thiết kế theo từng dự
  án: theme, nhãn trạng thái, mẫu trang nằm trong repo này, không đưa lên `codebase`.
  Bản tham chiếu đã làm cùng ngôn ngữ đó: `dw-proterial` commit `92b155a` (theme trong
  `@dw/ui`: Be Vietnam Pro, JetBrains Mono, primary `#006edc` vì `#0071e3` chỉ đạt 4,31:1;
  `StatusTag` theo bảng màu nhãn; navbar theo context; mẫu trang danh sách và chi tiết).
  Không chờ các ticket antd-shell 07/08/09 của nền tảng: phần cần dùng dựng ngay trong
  repo này.
