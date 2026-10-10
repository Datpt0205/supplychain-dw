# 06 — Dọn: quyền của dw_provisioner một chủ, ADR về package, E2E tự dọn, mypy, vitest chậm

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

- Danh sách quyền của `dw_provisioner` có ba bản không khớp nhau (baseline,
  `scripts/create_provisioner_role.py`, `test_provisioning.py`), lệch nhau ở `users` và
  `plans` (mục Open).
- ADR riêng của context (0016–0019, 0021) nằm ở `docs/adr/`, trái `docs/agents/domain.md`.
- Bộ Playwright để lại một Hồ sơ phát triển `E2E-…` mỗi lần chạy trong DB dev.
- mypy có kiểm `tests/integration` không.
- Vitest "proposes with no PIC field…" vượt 30 s khi máy bận.

## Tiêu chí chấp nhận

- [x] Một chủ: hàm `platform.grant_provisioner_privileges()` (migration `f38f027d8342`);
      migration, script và fixture test đều gọi nó; test khẳng định đúng tập quyền, không
      quyền nào trên bảng nghiệp vụ; mutation đỏ.
- [x] ADR 0016–0019, 0021 ở `packages/python/dw_supply_chain/docs/adr/`, giữ số; mọi link
      hai chiều sửa; `CONTEXT-MAP.md` nói chỗ mới.
- [x] Playwright dọn hồ sơ của chính nó sau khi đi (`e2e-cleanup <mã>`); lệnh không mã
      dọn phần sót của lần chạy hỏng.
- [x] mypy kiểm cả `apps/worker/tests/integration` và `apps/docgen/tests/integration`;
      lỗi lộ ra đã sửa.
- [x] Test vitest nhanh vì truy vấn rẻ, không vì nới thời gian.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- **Quyền của provisioner:** chọn bản hẹp hơn ở chỗ lệch, theo đúng việc code làm
  (`SqlProvisioningRepository` không bao giờ ghi `users`, `plans`): đọc `roles`, `users`,
  `plans`; ghi `tenants`, `workspaces`, `memberships`, `entitlements`; thêm/xóa
  `platform_operators`, `support_staff`; thêm `provisioning_audit` (append-only, baseline
  từng cho cả UPDATE/DELETE), `audit_events`; đọc, tạo, cập nhật
  `tenant_offboarding_requests`; đọc `support_grants` và cập nhật năm cột phân công. Hàm
  thu hồi mọi quyền role đang có trên mọi bảng rồi cấp đúng danh sách, nên chạy bao nhiêu
  lần cũng cùng kết quả; `SECURITY INVOKER`, thu hồi `EXECUTE` của PUBLIC. Migration sau
  cần thêm quyền thì thay hàm (CREATE OR REPLACE) và gọi lại. Script dừng với lời nhắc
  nếu hàm chưa có (chưa migrate). DB dev đã được thu hẹp khi migrate.
- **ADR:** 0020 ở lại `docs/adr/`: `required_scope` được đóng dấu là cơ chế duyệt của nền
  tảng (bản sinh đôi là ADR 0004). Link được viết lại bằng script, giải từng đường dẫn
  tương đối từ chỗ cũ rồi tính lại từ chỗ mới.
- **E2E:** `scripts/seed_supply_chain_demo.py e2e-cleanup [E2E-<mã>]` (chạy bằng
  migrator): xóa hồ sơ phát triển `E2E-…` của Alpha, yêu cầu duyệt nêu chúng (quyết định
  và biên nhận xem cascade), thông báo trỏ tới chúng; con của hồ sơ đi theo cascade, object
  của giấy tờ để lane dọn mồ côi; audit giữ nguyên (append-only). Hồ sơ đã ĐẶT HÀNG được
  giữ và nêu tên. `afterAll` của spec gọi với mã của chính lần chạy. Đã chạy trên DB dev:
  24 hồ sơ cũ được xóa.
- **mypy:** `files` thiếu test integration của apps (`apps/worker`, `apps/docgen`); thêm
  vào và thêm `apps/worker/tests/integration` vào `mypy_path` (một test import test
  khác). Lộ ra: một helper không có kiểu trả về (`_lane` → `Callable[[], Awaitable[None]]`),
  hai `create_task` nhận `Awaitable` (đổi `ensure_future`), sáu `type: ignore` thừa.
  `conftest.py` vẫn ngoài (tên module trùng nhau giữa các thư mục).
- **Vitest:** đo từng bước: `screen.getAllByRole("button", { name })` mất 4,6 s mỗi lần
  trên trang danh sách (tính tên và độ ẩn của mọi phần tử), và `waitFor` gọi nó lặp.
  Thay bằng tìm theo nhãn của nút (`getAllByText(..., { selector: "button span" })`):
  11 187 ms → 675 ms; hai test khóa 4–5 s → 183/190 ms. Mutation bỏ `disabled` của nút →
  2 test đỏ.
- **Thấy, chưa sửa:** `po-case-pages.test.tsx` in một unhandled rejection
  "message.error is not a function" (trang danh sách render ngoài `<App>` trong test
  "shows the server's sentence on a failed load…"); có từ trước HR1, test vẫn xanh.
