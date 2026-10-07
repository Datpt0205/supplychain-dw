# 01 — Dựng lại sáu trang và tám component Supply Chain bằng antd

Status: resolved
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

- [x] Test vitest có sẵn của từng trang vẫn xanh (sửa selector, không sửa ý); mỗi trang
      thêm test cho trạng thái rỗng và lỗi.
- [x] Không còn `lucide-react` và thành phần shadcn trong hai thư mục (lệnh `rg` của
      spec).
- [x] Playwright: mỗi trang đứng ở 320 px và ở hai phía mốc `lg`; mọi điều khiển tới
      được bằng `getByRole` với tên tiếng Việt.
- [x] Không màu, cỡ chữ, bo góc nào ngoài theme (`ui-quality.md` mục 1).
- [x] `pnpm lint`, `pnpm typecheck`, `pnpm build` xanh (build: biên dịch và kiểm kiểu
      xanh, dừng ở bước chép standalone vì EPERM symlink trên Windows, như lần trước).

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
- 2026-10-08, agent: tiêu chí Playwright viết và chạy. `apps/web/e2e/supply-chain.spec.ts`;
  bề rộng là các project `sc-320`, `sc-991`, `sc-992` của `playwright.config.ts` (một chủ
  của danh sách viewport, ui-quality mục 12; `sc-992` chạy múi giờ `Asia/Tokyo` và kiểm
  "Bạn xem lúc … (giờ Việt Nam)" theo giờ Việt Nam). Đăng nhập qua Keycloak (Linh, Khánh
  không có trong roster dev-login), web và API chế độ `oidc` như `make dev`, API có
  `DW_APPROVAL_CODE_SECRET`. Dữ liệu: `seed`, `elmich-sla`, `elmich-packaging`,
  `keycloak_dev_users.py`, và lệnh mới `seed_supply_chain_demo.py e2e-fixtures` (Khánh nối
  Zalo với chat giả `e2e-chat-khanh` và là BGĐ ở workspace thứ hai "Kho Hưng Yên", để
  /approvals/[id] cấp được mã và /settings hiện ô chọn workspace). Mỗi project đi một hồ sơ
  thật: An mở Bản tin, danh sách và chi tiết Hồ sơ PO (thẻ Chứng từ), đề xuất SP (dialog)
  và Yêu cầu mẫu (form bước); Linh Đã nhận mẫu, Mẫu đạt với biên bản tải lên ngay trong form
  (nút bước khóa tới khi có file); Khánh thấy thẻ ở /approvals khóa có lý do tới khi có nhận
  xét, mở /approvals/[id], "Lấy mã để quyết qua Zalo" khóa có lý do rồi cấp mã `DUYỆT
<6 số>`; /settings có thẻ Zalo và "Workspace dùng cho Zalo". Mỗi trang: h1, navbar đúng
  phía `lg` ("Mở menu" dưới 992, menu ngang từ 992), mọi điều khiển tìm bằng `getByRole`
  tên tiếng Việt, không cuộn ngang, không chữ bị cắt (ô ẩn tràn, hoặc dấu … không có
  `title`), ảnh chụp đính vào report.
  Lần chạy đầu đỏ, lỗi thật đã sửa: (1) 320 px: navbar tên tiếng Anh ("Open menu", "Main
  navigation") → "Mở menu", "Điều hướng chính"; (2) 320 px: tiêu đề thẻ Bản tin bị cắt "1
  case cần nhắc nhà c…" không có cách xem đủ → tiêu đề xuống dòng; (3) ô tìm của hai danh
  sách có nút tên "search" tiếng Anh và không làm gì (lọc theo gõ) → ô `type="search"` có
  icon, không nút; (4) /settings không có h1 → `PageHeader`; (5) /approvals: hai thẻ cùng
  tên link "Yêu cầu phê duyệt" → tiêu đề theo loại ("BGĐ duyệt mẫu", "Ký duyệt hồ sơ sản
  phẩm", qua plug-in point `APPROVAL_TITLE`), và "Duyệt"/"Từ chối" bị khóa khi thiếu nhận
  xét mà không có lý do → tooltip và câu bên cạnh; (6) mục menu đọc kèm tên glyph ("read Bản
  tin hôm nay") → icon `aria-hidden`; (7) 992 px với Khánh (tên dài, hai workspace): mọi mục
  menu tràn vào "…" → chip tài khoản chỉ còn avatar giữa `lg` và `xl`, nút có tên "Tài
  khoản: <tên>, <vai>" (trước đó tên nút là chữ cái "N" ở 320 px). Đột biến: bỏ từng sửa (1),
  (2), (5) lý do nhận xét, (6) thì test đỏ; (3), (4) và tên link (5) đỏ ở lần chạy đầu.
  Lần chạy cuối: 15 passed (5 test × 3 project, 5,4 phút). Project `chromium` (spec có
  sẵn): `antd-shell` 11/11 xanh với web chế độ dev; `feedback` 2/3, test "admin inbox" đỏ
  vì seed Supply Chain đổi vai của Diệu (`dev|dieu.hoang`, admin trong spec) thành
  `sc_finance` — xung đột dữ liệu seed có từ trước, không thuộc W. Lần upload đầu trong
  form Mẫu đạt treo một lần (request không tới log API), lần sau và mọi lần chạy sau đều
  201; không tái hiện được. Còn lại, ngoài các trang của W: chuông thông báo, nút
  Feedback, menu tài khoản và nhãn vai ("Staff", "Manager", `sc_operator` ở /settings) của
  shell nền tảng vẫn tiếng Anh hoặc mã; nút Feedback nổi đè nội dung ở 320 px; dòng chờ BGĐ
  ở chi tiết hồ sơ ghi mã quyền `supply_chain.approve.bod` (có chủ ý theo comment code). Mỗi
  lần chạy suite để lại 3 hồ sơ E2E-… chờ BGĐ trong DB dev.
