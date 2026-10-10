# 01 — Miễn trừ tách nhiệm cần người thứ hai; đổi scope của role kiểm lại membership

Status: resolved (2026-10-08, nhánh `feat/security-debts`)
Blocked by: —
Area: platform-runtime

## Mục tiêu

- Một org admin tự miễn một quy tắc rồi tự giữ cả hai vế: kiểm soát cần hai người bị một
  người gỡ.
- SoD chỉ kiểm khi ghi membership. Migration nới scope của role/permission set làm mọi
  membership đang giữ nó vi phạm mà không ai biết.

## Việc đã làm

- Migration `f381f1694395` (idempotent): `confirmed_by`, `confirmed_at`, `confirm_reason`;
  CHECK `ck_sod_waivers_second_person`; `sod_violation` chỉ nhận miễn trừ đã xác nhận;
  guard: insert không được kèm xác nhận, miễn trừ mở chỉ được xác nhận một lần và thu hồi
  một lần; trigger `trg_roles_sod_memberships` / `trg_permission_sets_sod_memberships`.
- `SeparationOfDutiesService.confirm`, `POST .../waiver/confirm`, audit
  `platform.sod.waiver_confirm`; danh sách trả `confirmed_by`/`confirmed_at`; trang web hiện
  "Awaiting confirmation" với nút xác nhận và rút; client + contract sinh lại.
- ADR 0012.

## Quyết định tạm (Đạt giao)

- **Xác nhận của admin thứ hai, không phải strict approval:** ít máy móc hơn (bảng, trigger
  và service đã sở hữu quyết định này; approval cần thêm loại approval run-less, prefix,
  handler outbox ở worker). Xác nhận bắt buộc có lý do, như ghi chú của strict approval.
- **Miễn trừ đang mở trước migration:** giữ mở nhưng chưa xác nhận, không còn gỡ quy tắc
  cho lần ghi membership mới tới khi admin thứ hai xác nhận (fail closed). Membership đang
  giữ hai vế không bị đụng.
- **Đổi scope role:** từ chối (không gắn cờ). Role và permission set chỉ đổi trong
  migration, nên từ chối là migration hỏng, có tên quy tắc và tối đa 20 membership id trong
  DETAIL. Hôm nay không có đường HTTP nào ghi hai bảng đó; đường nào thêm sau thì dịch
  `ck_roles_sod_memberships` thành 409 kèm danh sách như đường thu hồi.
- Trang web vẫn dùng shadcn: chuyển sang antd thuộc ticket `web-ui/antd-shell`, không làm
  ở đây.
- Chưa phủ: đổi scope của chính một quy tắc (`platform.sod_rules`).

## Tiêu chí chấp nhận

- [x] Miễn trừ mới gỡ quy tắc chỉ sau khi admin khác xác nhận; người đề xuất xác nhận → 409;
      migrator cũng không xác nhận thay người đề xuất hay chèn sẵn xác nhận.
- [x] Xác nhận cần scope, lý do, và một miễn trừ đang chờ; xác nhận hai lần → 404.
- [x] Nới role hoặc permission set làm membership vi phạm → bị từ chối, nêu membership; có
      miễn trừ đã xác nhận → được.
- [x] Mutation: miễn trừ chưa xác nhận vẫn gỡ → 2 đỏ; bỏ CHECK người thứ hai → 1 đỏ;
      trigger scope không bao giờ từ chối → 3 đỏ; insert được kèm xác nhận → 1 đỏ.
