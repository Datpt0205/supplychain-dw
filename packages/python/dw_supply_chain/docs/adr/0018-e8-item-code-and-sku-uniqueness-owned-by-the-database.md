---
status: Accepted
date: 2026-10-05
source:
    - ../../../../../docs/products/elmich/process.md#32-bảng-17-bước # bước 9: kiểm tra không trùng mã hàng/mã SKU
    - ../../../../../.claude/rules/code-quality.md # Single responsibility: database refuses by constraint
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

## Sửa đổi 2026-10-07 (tạm, lát S4; Đạt ủy quyền quyết các điểm mở)

Làm theo quyết định ở trên, với ba điểm thu hẹp. Chi tiết và test ở Comments của
`.claude/plans/supply-chain/stage-1/issues/04-item-code-sku-signoff-step-9.md`.

1. **Khóa ngoại:** mã hàng và SKU tham chiếu hồ sơ `ON DELETE CASCADE` (như mọi bảng con
   của hồ sơ, migration `59e69efdfa37`), SKU tham chiếu mã hàng của CHÍNH hồ sơ bằng FK ghép
   `(tenant_id, workspace_id, product_dev_case_id, item_code_id)` `ON DELETE NO ACTION`,
   không phải RESTRICT. Purge offboarding chỉ xóa bảng `dw_app` được DELETE và dựa vào
   cascade của hồ sơ; RESTRICT sẽ chặn chính cascade đó. NO ACTION kiểm ở cuối câu lệnh, nên
   mọi lệnh xóa mã hàng còn SKU vẫn bị từ chối. `dw_app` không có DELETE trên `item_codes`
   (chỉ UPDATE cột `code`, để sửa mã trước khi trình ký), có DELETE trên `skus` (bỏ SKU).
2. **QE-11 chưa trả lời: chưa có policy định dạng.** Đề xuất "pattern của tenant, mặc định
   thoáng" không làm: quyết định ở trên đã nói không kiểm định dạng cho tới khi có quy tắc,
   một regex do tenant đặt cần chặn ReDoS riêng, và policy chưa ai ghi đè là thứ không ai
   đọc. Mã phân biệt hoa thường (UNIQUE thường). Khi Elmich trả lời QE-11, định dạng (và
   việc so không phân biệt hoa thường, nếu cần) vào policy của tenant hoặc một index trên
   biểu thức, bằng migration mới.
3. Lỗi trùng là 409 nêu mã (`details.item_code` hoặc `details.sku_code`) và tên ràng buộc;
   trang hiện câu đó tại trường. Hồ sơ bị hủy giữ mã hàng và SKU: mã đã cấp không tái dùng
   trong tenant.

## Sửa đổi 2026-10-09 (tạm, lát AI-13; Đạt: quy tắc tạm, thoáng tới QE-11)

1. **Quy tắc mã là policy của tenant, để đề xuất, không để kiểm.** `supply_chain_item_code_rule@1.0.0`
   (nền tảng: `rule: null`) là một mẫu (tiền tố, dấu nối, số chữ số; dấu nối và số chữ số của
   SKU), không phải regex do tenant gõ, nên không có ReDoS. Người đọc nó là bước chuẩn bị bước 9
   (`application/item_coding.py`): mã tiếp theo là số lớn nhất đã dùng với tiền tố đó, trong
   ứng dụng và trong danh mục đã nạp (ADR 0027), cộng một; SKU là mã hàng + số thứ tự biến thể
   BM04. Không quy tắc thì không đề xuất mã (không đoán, không hỏi mô hình). `issue_item_code`
   vẫn không kiểm định dạng: mã người sửa trong bản nháp hay gõ tay vẫn được, cơ sở dữ liệu vẫn
   là người trả lời "đã có chưa". Elmich ghi đè tạm `EL-00001` / `EL-00001-01`
   (`scripts/elmich_item_code_rule_override.yaml`) tới khi trả lời QE-11. Đọc và thay qua
   `GET|PUT /item-code-rule` (`action_duties.read|write`).
2. **Trùng với danh mục đã nạp là trùng.** Phép kiểm `codes_free` nêu từng mã hàng, mã SKU mà
   hồ sơ khác (trong ứng dụng) hay danh mục đã nạp giữ; chủ thể của đề xuất buộc câu trả lời đó,
   nên mã bị chiếm sau khi trình làm quyết định hết hiệu lực; khi duyệt, code hỏi lại và từ chối
   (409 nêu mã) trước khi ghi gì. Mã của chính hồ sơ không tính là trùng. Mã do workspace khác
   của tenant giữ không thấy được ở đây (RLS); UNIQUE theo tenant vẫn từ chối khi ghi.
3. **Nhiều bước một lần ghi.** Duyệt đề xuất bước 9 cấp mã, thêm từng SKU còn thiếu rồi trình ký
   trong một giao dịch; câu UPDATE có điều kiện so `version - max(1, số bước)` (mỗi bước tăng
   phiên bản một lần; đổi PIC không là bước nhưng cũng tăng một). Ràng buộc trùng được nêu theo
   mã của bước tương ứng trong cả nhóm bước, không chỉ bước cuối.
