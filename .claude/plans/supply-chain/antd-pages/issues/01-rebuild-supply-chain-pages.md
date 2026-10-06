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
- 2026-10-06, agent: dựng xong trong một commit (Đạt yêu cầu một commit, không phải mỗi
  trang một commit). `@dw/ui`: theme v3 sáng và tối (primary sáng `#006edc`, viền ô
  `#8c8c91`, nút viên thuốc, Table/Segmented/Breadcrumb theo bản vẽ), JetBrains Mono qua
  next/font làm `fontFamilyCode`, `StatusTag` đọc `STATUS_TONES` theo chế độ màu,
  `PageHeader` (breadcrumb, dòng loại, tiêu đề h1, tóm tắt, hành động), `RegionState`
  với một bảng mã lỗi → trạng thái (403, gói, 404, xung đột, 500 kèm `request_id`, mất
  mạng). `lib/dates.ts` giờ trước, theo `Asia/Ho_Chi_Minh`, "(giờ Việt Nam)";
  `lib/search.ts` tìm bỏ dấu; `<html lang="vi">`; bỏ các luật bảng không layer trong
  `globals.css` (ui-quality mục 11). Navbar: ai chỉ tới Supply Chain và không quản trị
  thấy menu của context cùng trang Duyệt (BGĐ quyết ở đó), thương hiệu "Digital Worker ·
  Supply Chain". Trang: danh sách và chi tiết Hồ sơ PO (dải tóm tắt bước/SLA/NCC/chờ
  duyệt, "Máy đọc" và trích dẫn ở cập nhật NCC, "AI viết" ở phân tích trễ), danh sách và
  chi tiết hồ sơ phát triển SP, thẻ Chứng từ, Cần chú ý, Control Tower và thanh hỏi, Bản
  tin hôm nay và tóm tắt AI, Việc cần làm, `/approvals` (chỉ giao diện; logic khóa giữ
  nguyên, nút đổi tên "Duyệt"/"Từ chối"). Nhãn trạng thái vẫn một chủ:
  `CASE_STATE_LABEL`, `PRODUCT_DEV_STATE_LABEL`; test so từng nhãn với bảng của
  `CONTEXT.md`. Lệnh: `make lint` xanh; `make typecheck` xanh (mypy 476 file, tsc 4 gói);
  `make test-unit` 1920 passed, 3 skipped; `pnpm --filter @dw/web exec vitest run` 30
  file, 283 test xanh (trước: 22 file, 125); `pnpm --filter @dw/web build` biên dịch,
  kiểm kiểu và dựng 26 trang, rồi dừng ở bước chép standalone vì EPERM symlink trên
  Windows (đã biết). Đã thử đột biến: đổi một nhãn, một tông tối, bỏ khóa mất mạng ở Việc
  cần làm, bỏ `permission_denied` khỏi bảng, bỏ khóa Duyệt: mỗi cái làm test đỏ.
  Chưa đạt, nên chưa `resolved`: tiêu chí Playwright (320 px, hai phía `lg`,
  `getByRole` tiếng Việt) chưa viết và chưa chạy, cần stack đang chạy; không có
  Playwright project theo bề rộng. Dữ liệu API không có nên không vẽ: số đếm trên tab
  theo trạng thái và khung "Cần xử lý" ở trang danh sách (API phân trang keyset, không
  trả số theo trạng thái); bảng không chuyển thành thẻ dưới ~1100 px (cuộn ngang, cột
  phụ ẩn theo `responsive`).
