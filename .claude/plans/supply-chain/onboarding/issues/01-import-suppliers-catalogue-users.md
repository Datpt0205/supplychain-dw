# ON-01 — Nạp NCC, danh mục mã hàng/SKU, người dùng (E16)

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md
Area: supply-chain

## Mục tiêu

Dữ liệu Elmich đang có vào hệ thống một lần, qua đúng handler, có chạy thử.

## Việc cần làm

1. Mẫu Excel (`docs/products/elmich/import-template.xlsx`) bốn sheet; lệnh
   `scripts/import_supply_chain.py dry-run|apply <file>`; scope `supply_chain.import`.
2. NCC (tên, mã, liên hệ, tài khoản), bảng `catalogue_items` (mã hàng, SKU, tên, Category)
   mà kiểm trùng bước 9 đọc; người dùng qua provisioning + Keycloak.
3. Báo cáo từng dòng; idempotent theo mã ngoài; audit actor là người chạy.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Unit xanh: hàng của workspace khác và tenant khác không được tìm thấy, không bị chạm. Integration viết, chưa chạy: `test_data_import.py` (RLS đọc, WITH CHECK ghi), `test_privileges.py` mục mới.)_
- [x] Chạy thử không ghi gì (đếm dòng trước/sau). _(Unit: fake đếm 0 lượt ghi; integration đếm bảng trước/sau, chưa chạy.)_
- [x] Chạy lại cùng file không tạo bản sao.
- [x] Dòng xấu được báo, không làm hỏng dòng khác.
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0027 (E16); HR4 (danh mục NCC).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát ON-01 (agent).** Đã làm (không Docker):

- Màn "Nạp dữ liệu" (`/supply-chain/import`, antd, dáng v3; menu Supply Chain, scope
  `supply_chain.import`): tải mẫu, chọn file, **Chạy thử** (không ghi gì), xem tổng theo sheet và
  từng dòng (Sẽ thêm / Đã có, bỏ qua / Thêm một phần / Từ chối kèm lý do), rồi **Nạp thật** chỉ
  mở sau khi chạy thử đúng file đó (lý do bằng chữ khi khóa, xác nhận nêu tên file, nút Hủy là
  mặc định). Lệch ticket: không có `scripts/import_supply_chain.py` (Đạt muốn màn quản trị,
  ADR 0027 sửa đổi 1).
- Mẫu Excel ba sheet (NCC, Danh mục, Người dùng) dựng từ `SHEETS` (`domain/data_import.py`);
  `docs/products/elmich/import-template.xlsx` ghi bằng `scripts/build_import_template.py`, test
  đỏ khi lệch. Sheet hồ sơ đang chạy là ON-02.
- `ImportSupplyChainData` (`application/data_import.py`): NCC theo mã (tạo, hoặc gán mã cho NCC
  ứng dụng đã tạo theo tên; tên của NCC khác hay mã khác là từ chối), liên hệ và tài khoản qua
  `SaveSupplierContact`/`SaveSupplierBankAccount` (cần `commercial.write`; tài khoản so bằng
  `same_account`, khác thì từ chối, không bao giờ in số), danh mục theo (mã hàng, SKU), người
  dùng qua dịch vụ thành viên của nền tảng (`PlatformMemberDirectory`: không vai quản trị,
  workspace phải thuộc công ty, đã là thành viên thì bỏ qua). Trùng trong file: dòng sau bị từ
  chối nêu dòng trước. Mỗi ghi có audit actor là người bấm (`via: import`).
- Migration `0f231b1bf02d`: `catalogue_items` (workspace RLS FORCE, UNIQUE NULLS NOT DISTINCT
  (mã hàng, SKU), UNIQUE SKU, CHECK mã đã cắt khoảng trắng), `dw_app` SELECT/INSERT/DELETE (chỉ
  cho purge), UPDATE cột `suppliers.code`, `supply_chain.import` cho `org_admin`.
- Bộ đọc openpyxl (`adapters/import_workbook.py`): `data_only`, `read_only`, giới hạn dòng/cột,
  zip giải nén > 64 MiB bị từ chối trước khi mở; file không phải xlsx là 422.
- Route `GET /imports/template`, `POST /imports/dry-run`, `POST /imports` (Idempotency-Key, thân
  ≤ 5 MiB + multipart). OpenAPI và client sinh lại; vitest trang 4 ca.

Mutation (control xanh trước; `test_data_import.py`): bỏ kiểm scope nạp; chạy thử vẫn tạo NCC;
chạy thử vẫn ghi danh mục; mọi tài khoản là "khớp"; tài khoản khác ghi đè; bỏ kiểm trước quyền
thương mại (sống sót lần đầu: handler vẫn từ chối khi nạp thật; thêm ca chạy thử không quyền
rồi đỏ); trùng trong file giữ lại; vai quản trị/lạ được nhận; workspace ngoài công ty được nhận;
tên của NCC khác được nhận (sống sót lần đầu; thêm ca rồi đỏ); gán mã đè mã khác; SKU dưới mã
hàng khác được nhận (sống sót lần đầu: UNIQUE vẫn từ chối khi nạp thật; thêm ca chạy thử rồi
đỏ); ô bắt buộc không bắt buộc; tiêu đề lạ được nhận; bỏ kiểm zip giải nén. 15/15 đỏ.

`reviewing-feature-security`: (1) bảng mới hẹp theo tenant VÀ workspace, ghi qua
`tenant_session`, workspace người dùng phải thuộc tenant (dịch vụ nền tảng); (2) `supply_chain.import`
chỉ cho chạy, từng phần còn cần quyền riêng; vai quản trị không cấp qua nạp; (3) không mô hình,
không tool; (4) không nội dung file nào tới mô hình hay prompt; (5) mỗi ghi có audit, idempotent
theo khóa, không gửi gì ra ngoài; (6) đóng an toàn: ô thiếu là từ chối, tài khoản khác là từ
chối, file lạ là 422, zip phình là từ chối. `reviewing-deployment-security`: ba route mới đều
qua `get_access_context`, thân bị chặn theo `Content-Length` trước khi parse, không URL ra ngoài.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): `tests/integration/test_data_import.py`
(RLS và WITH CHECK của `catalogue_items`, UNIQUE và CHECK, khớp tên bằng hàm của cơ sở dữ liệu,
`assign_code` một lần, chạy thử đếm bảng trước/sau, chạy lại không thêm), mục mới của
`test_privileges.py`, `test_rls_coverage.py` với bảng mới, `alembic upgrade head` qua
`0f231b1bf02d`. Người dùng: tài khoản Keycloak do người vận hành IdP tạo (hoặc tự đăng ký);
nền tảng chưa có cổng quản trị Keycloak (ADR 0027 sửa đổi 6).
