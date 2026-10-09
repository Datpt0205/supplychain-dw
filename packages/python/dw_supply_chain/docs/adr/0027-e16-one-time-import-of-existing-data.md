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
