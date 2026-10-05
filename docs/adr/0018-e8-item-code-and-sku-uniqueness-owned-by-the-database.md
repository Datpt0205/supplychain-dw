---
status: Proposed
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # bước 9: kiểm tra không trùng mã hàng/mã SKU
    - ../../.claude/rules/code-quality.md # Single responsibility: database refuses by constraint
---

# E8. Cơ sở dữ liệu sở hữu việc không trùng mã hàng và mã SKU

Bước 9 đòi "kiểm tra không trùng mã hàng/mã SKU" và "SKU chỉ thêm sau khi có mã hàng
chính thức". Quyết định:

- `supply_chain.item_codes`: UNIQUE `(tenant_id, code)`.
- `supply_chain.item_codes` thêm UNIQUE `(tenant_id, product_dev_case_id)`: một hồ sơ
  phát triển có một mã hàng chính thức.
- `supply_chain.skus`: UNIQUE `(tenant_id, sku_code)`; `item_code_id` FK NOT NULL
  `ON DELETE RESTRICT`, có index; `planned_quantity` NULL hoặc `> 0`. "Chốt số lượng
  SKU" của bước 9 có thể là số biến thể hoặc số lượng đặt mỗi SKU (QE-11); số lượng
  đặt thật nằm ở dòng PO ([ADR 0017](0017-e7-hand-off-via-order-requested.md)).
- Aggregate từ chối `add_sku` trước `issue_item_code`; FK NOT NULL giữ điều đó cả khi
  ai đó ghi thẳng vào bảng.
- Adapter đổi lỗi vi phạm ràng buộc thành `ConflictError` theo **tên ràng buộc**
  (lấy từ `NAMING_CONVENTION`), cách mã tách nhiệm đã làm. Giao diện có thể hỏi trước
  cho tiện, nhưng câu trả lời có giá trị là của database: hai người bấm cùng lúc thì
  một người nhận 409.
- Mã được cắt khoảng trắng hai đầu và CHECK khác rỗng. Không kiểm định dạng cho tới
  khi Elmich gửi quy tắc mã (QE-11); khi có, quy tắc là policy của tenant, không viết
  cứng trong code.

## Phương án đã cân nhắc

- **Kiểm trùng trong ứng dụng bằng SELECT trước INSERT.** Bác: hai giao dịch song song
  cùng thấy "chưa có".
- **UNIQUE theo workspace.** Chưa chọn: Elmich là một công ty, một tenant; mã hàng là
  mã của công ty. Đổi khi Elmich nói khác (QE-11).

## Hệ quả

- Không đồng bộ với ERP. Nếu Elmich có danh mục hàng ở hệ thống khác, đồng bộ là một
  adapter sau port, không đổi ràng buộc này.
