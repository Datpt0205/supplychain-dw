---
status: Accepted (Đạt, 2026-10-09; mặc định của lead)
date: 2026-10-09
source:
    - ../../src/dw_supply_chain/domain/po_case.py # CreatePOCase mở ở po_created
    - ../../../../../.claude/plans/supply-chain/onboarding/spec.md
---

# E16. Nạp một lần dữ liệu Elmich đang có

Elmich không bắt đầu từ số không: có NCC, danh mục mã hàng và SKU, sản phẩm đang lấy mẫu
và PO đang sản xuất. Hôm nay không có gì nạp chúng; Hồ sơ PO chỉ mở ở `po_created`, hồ sơ
phát triển chỉ ở `proposed`.

**Quyết định:**

1. **Nạp từ Excel theo mẫu repo cung cấp**, một lần, không tích hợp ERP. Bốn sheet: NCC
   (tên, mã, người liên hệ, tài khoản), danh mục (mã hàng, SKU, tên, Category), người dùng
   (email, vai, workspace), hồ sơ đang chạy.
2. **Chạy thử trước:** lệnh trả báo cáo từng dòng (nhận, từ chối kèm lý do) mà không ghi gì;
   chạy thật ghi qua đúng handler của ứng dụng (kiểm, quyền, audit), actor là quản trị viên
   chạy lệnh, scope `supply_chain.import`. Idempotent theo mã ngoài của từng dòng.
3. **Hồ sơ đang chạy** mở ở trạng thái hiện tại với **một** dòng lịch sử `import`
   (`from_state` NULL, lý do "nạp từ dữ liệu cũ"), ngày vào trạng thái lấy từ sheet và
   đánh dấu là khai báo; mốc SLA chạy từ ngày đó. Không dựng lịch sử giả cho các bước trước.
4. **Danh mục** là bảng của tenant mà phép kiểm trùng mã ở bước 9 đọc cùng `item_codes`;
   mã đã có trong danh mục là trùng.
5. **Người dùng:** tạo trong Keycloak và membership qua đường provisioning của nền tảng;
   liên kết Zalo vẫn do chính người đó làm (ADR 0012).

## Hệ quả

- Ticket `onboarding/issues/01`, `02`. Nạp lại là chạy lại cùng file: dòng đã có bỏ qua.

## Sửa đổi 2026-10-09 (tạm, lát ON-01; lead quyết các điểm ADR để mở)

1. **Màn quản trị thay lệnh dòng.** Theo yêu cầu của Đạt, nạp chạy trên màn "Nạp dữ liệu"
   (`/supply-chain/import`, antd, dáng v3) qua `POST /imports/dry-run` và `POST /imports`;
   không có `scripts/import_supply_chain.py`: actor là người bấm (đã xác thực), không phải
   một tài khoản dòng lệnh, và RLS theo tenant, workspace của người đó. Mẫu Excel do code
   dựng từ `SHEETS` (`domain/data_import.py`, một chủ): màn tải về, `docs/products/elmich/
import-template.xlsx` ghi bằng `scripts/build_import_template.py`, bộ đọc so tiêu đề cột
   với cùng khai báo; test đỏ khi bản trong docs lệch.
2. **Ba sheet ở lát này** (NCC, Danh mục, Người dùng). Sheet hồ sơ đang chạy đến với ON-02.
3. **Quyền:** `supply_chain.import` (vai `org_admin`, migration `0f231b1bf02d`) để chạy;
   mỗi phần của dòng đi qua handler hay dịch vụ chủ của nó với quyền của chính nó: liên hệ
   và tài khoản NCC qua `SaveSupplierContact`, `SaveSupplierBankAccount`
   (`supply_chain.commercial.write`), người dùng qua dịch vụ thành viên của nền tảng
   (`platform.members.read|write`, không vai quản trị). Thiếu quyền thì phần đó của dòng bị
   từ chối có lý do, phần khác vẫn chạy (cả khi chạy thử).
4. **Khóa idempotent:** NCC theo mã (`uq_suppliers_tenant_id_workspace_id_code`); NCC ứng
   dụng đã tạo theo tên (từ hồ sơ) được gán mã, chỉ khi chưa có mã (`dw_app` được UPDATE cột
   `code`, câu lệnh chỉ chạm mã NULL); tên trùng NCC đã có mã khác thì từ chối. Danh mục theo
   (mã hàng, SKU), UNIQUE NULLS NOT DISTINCT; một SKU chỉ dưới một mã hàng. Người dùng theo
   email; đã là thành viên thì bỏ qua (không đổi vai qua nạp).
5. **Tài khoản NCC:** so bằng `same_account` với tài khoản đang lưu; khác thì từ chối (đổi nơi
   nhận tiền không phải việc của nạp), báo cáo không bao giờ in số tài khoản.
6. **Keycloak:** người dùng được tạo như lời mời của Org Admin (`platform.users` + membership,
   audit từng membership); đăng nhập lần đầu bằng email đã xác minh liên kết tài khoản
   (`identity_provisioning.py`). Nền tảng chưa có cổng quản trị Keycloak; tạo tài khoản
   Keycloak (hoặc bật tự đăng ký) là việc của người vận hành IdP, ghi trong ticket.
7. **Giới hạn:** file ≤ 5 MiB, ≤ 5.000 dòng mỗi sheet, zip giải nén ≤ 64 MiB (từ chối trước
   khi openpyxl mở); dòng trống bỏ qua; ô bắt buộc trống, quá dài, có ký tự điều khiển là dòng
   bị từ chối; tiêu đề lạ hay thiếu là cả sheet không đọc. Không mô hình nào đọc file.
