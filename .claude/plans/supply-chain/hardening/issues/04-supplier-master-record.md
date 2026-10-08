# 04 — Hồ sơ gốc nhà cung cấp (tạm, tối thiểu)

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

Nhà cung cấp là chữ tự do trên từng hồ sơ: "Đông Á Inox" và "đông á inox " là hai NCC
khác nhau ở Control Tower, ở bộ lọc, ở câu hỏi Zalo/web (mục "Suppliers have no master
record" phần Open).

## Tiêu chí chấp nhận

- [x] Bảng `supply_chain.suppliers` theo tenant và workspace: `name`, `normalized_name`
      UNIQUE theo (tenant, workspace), `code` tùy chọn (UNIQUE khi có). RLS ENABLE + FORCE,
      hình workspace chuẩn; grant đi cùng migration (`a0035e9faf32`).
- [x] Hồ sơ PO và hồ sơ phát triển tham chiếu NCC (`supplier_id`); tên tự do được quy về
      NCC có sẵn theo tên chuẩn hóa, không có thì tạo (audit `supply_chain.supplier.created`
      tên người gọi, cùng transaction).
- [x] Migration backfill từ các tên đang có: một NCC cho mỗi nhóm tên chuẩn hóa mỗi
      workspace, lấy cách viết dùng sớm nhất.
- [x] Câu hỏi Zalo/web (`AnswerCaseQuery`) quy tên NCC trong câu hỏi về danh sách NCC gốc.
- [x] Negative: không bao giờ khớp NCC của tenant khác hay workspace khác; SQL không thể
      trỏ hồ sơ vào NCC của workspace khác (FK), không thể mang tên khác tên NCC.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- **Chuẩn hóa là của database** (`supply_chain.normalize_supplier_name`, IMMUTABLE):
  NFKC (no-break space thành space, ký tự tổ hợp thành ký tự dựng sẵn), gộp khoảng
  trắng, cắt hai đầu, chữ thường theo collation ICU gốc (không phụ thuộc locale của DB;
  đã đo trên Postgres 16 Alpine: `lower` của locale và của `und-x-icu` cho cùng kết quả
  với tiếng Việt, `\s` không bắt no-break space nên NFKC đi trước). `normalized_name` là
  cột GENERATED, UNIQUE: Python không chép lại quy tắc.
- **Dấu được giữ:** "Đông Á" và "Dong A" là hai NCC. Gộp chúng là việc của màn gộp (chưa
  làm), không phải đoán của hàm chuẩn hóa.
- **Hồ sơ giữ cột `supplier_name`**, nhưng FK `(tenant_id, workspace_id, supplier_id,
  supplier_name)` → `suppliers (tenant_id, workspace_id, id, name)` `ON UPDATE CASCADE ON
  DELETE RESTRICT` bắt nó luôn bằng tên đã lưu của NCC. Hai bản sao không thể lệch nhau,
  thay vì thêm join vào mọi câu đọc; mọi bộ lọc, nhóm và index theo tên giữ nguyên và
  nay khớp mọi cách viết đã quy về một NCC. `po_cases.supplier_id` NOT NULL; hồ sơ phát
  triển NULL tới bước 2 (`ck_product_dev_cases_supplier`: cả hai hoặc không).
- **Quy về NCC ở adapter, trong transaction của lệnh ghi hồ sơ**
  (`adapters/persistence/suppliers.resolve_supplier`): tìm theo tên chuẩn hóa, không có
  thì `INSERT … ON CONFLICT DO NOTHING` rồi đọc lại (hai người tạo cùng lúc ra một NCC).
  NCC được tạo ghi audit tên người gọi; NCC dùng lại không ghi gì. `save` của Hồ sơ PO
  không ghi lại NCC (không bước nào đổi NCC).
- **Câu hỏi:** `list_supplier_names` đọc bảng NCC của workspace thay vì DISTINCT trên
  hồ sơ. "Supplier update understanding" không nêu tên NCC (trích xuất chỉ có
  `affected_po`) nên không có gì để quy; phân tích trễ nhận tên đã lưu qua hồ sơ.
- **Grant:** `dw_app` SELECT, INSERT, DELETE (DELETE chỉ để offboarding purge, vốn xóa
  bảng nào `dw_app` được xóa; RESTRICT từ hồ sơ giữ NCC đang dùng; purge xóa
  `po_cases`, `product_dev_cases` trước `suppliers` theo thứ tự tên). Không UPDATE.
  `test_privileges.py` khẳng định.
- **Còn lại, chưa làm:** màn quản lý NCC (đổi tên, gộp hai NCC, gán `code`), so khớp bỏ
  dấu có người xác nhận; `code` có cột và ràng buộc nhưng chưa ai ghi (màn quản lý ghi).
  Đổi tên khi có sẽ cần grant UPDATE(name) và FK đã CASCADE sang mọi hồ sơ.
