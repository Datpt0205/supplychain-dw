# 14 — Bước 10: PO nháp

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md, .claude/plans/supply-chain/ai-automation/issues/12-step-8-supplier-confirmation.md
Area: supply-chain

## Mục tiêu

Vào `order_requested`, AI soạn PO từ BM04, điều khoản NCC đã chốt, SKU và số lượng; code tính tổng; Cung ứng duyệt `create_po`.

## Việc cần làm

1. Mẫu `purchase_order`; số PO theo quy tắc tenant hoặc người nhập.
2. Code tính tổng dòng, tổng PO, cọc theo `deposit_percent`.
3. Duyệt `create_po`: trường thương mại ghi vào Hồ sơ PO, chứng từ `purchase_order`; thông
   báo Kế toán.
4. QE-06: nếu Elmich muốn BGĐ duyệt PO trên ngưỡng, bật trong approval matrix (không code).

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới. Hồ sơ PO tenant khác không soạn; BM04 và thư chốt của workspace khác không dùng: unit và eval. Integration viết, chưa chạy.)_
- [x] Tổng do mô hình trả khác code: bản nháp bị từ chối. _(Không mô hình nào ở bước 10; tổng nào trong bản nháp khác số code tính, ai ghi cũng vậy, là phát hiện và bị từ chối khi duyệt.)_
- [x] Thiếu điều khoản: khoảng trống, PO vẫn soạn được để người điền.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments). _(Không có lượt gọi mô hình nào để đếm: đường PO không có gateway.)_
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- Slide 4 PoC (PO nháp); ADR 0017; ADR 0026.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát AI-14 (agent).** Đã làm (không Docker, không mô hình):

- Lệch ticket (ADR 0025 sửa đổi AI-14): PO nháp không qua approval run của nền tảng. Lane
  `supply_chain_purchase_orders` (worker, nhịp lane trích xuất) soạn MỘT `purchase_order` cho mỗi
  Hồ sơ PO `order_requested` khi policy chuẩn bị bước của tenant có `purchase_order: true` (Elmich
  override 1.7.0; nền tảng tắt); bản nháp bị từ chối thì không soạn lại. Báo Cung ứng (duty của
  `create_po`) và Kế toán một lần, không kèm giá.
- Giá trị (`domain/purchase_order_draft.py`, `application/purchase_orders.py`): điều khoản và đơn
  giá người đã nhập trên hồ sơ trước; rồi BM04 mới nhất (đơn giá, tiền tệ, Incoterm) trừ khi thư
  chốt NCC (đọc ở bước 8, so từng điều khoản như AI-12) ghi khác: mâu thuẫn, ô trống; ô chỉ thư
  nêu lấy từ thư. Điều khoản thanh toán, % cọc, ngày giao không đoán. Thành tiền, tổng, tiền cọc do
  code tính; mẫu `supply_chain.purchase_order@1.1.0` (thêm % cọc, tiền cọc; `deposit_amount` vào
  `PRICE_FIELDS`).
- Trang Hồ sơ PO: thẻ "PO nháp (AI soạn)" (`GET /po-cases/{id}/purchase-order`): phát hiện (còn
  thiếu, mâu thuẫn, tổng khác, thiếu BM04), không giá; ô số PO và "Duyệt PO" (khóa có lý do bằng
  chữ; xác nhận nêu số PO, Hủy là mặc định). Ô của bản nháp xem và sửa ở "Bản nháp chứng từ" (giá
  ẩn theo quyền). Thẻ tạo PO tay vẫn còn.
- Duyệt (`POST .../purchase-order/approval`): scope duty `create_po` VÀ `commercial.write`; bản nháp
  phải đúng phiên bản đã thấy (`content_sha256`); tổng khác code, dòng thiếu số lượng/đơn giá, thiếu
  tiền tệ thì từ chối trước khi dựng gì. Một giao dịch (`SqlPurchaseOrderOutcomes`): phiên bản mang
  số PO + xác nhận, chứng từ `purchase_order` (`ai_prepared`), `create_po` (`save_in`), điều khoản
  và đơn giá (`write_terms`), audit. Số PO đã có trong công ty: không gì được ghi. Kế toán được báo
  sau, không giá.
- File `purchase_order` cần `commercial.read` để tải (`PRICED_DOCUMENT_TYPES`; ADR 0026 sửa đổi
  AI-14). QE-06 (BGĐ duyệt PO trên ngưỡng): chưa bật; ma trận approval từ chối `create_po` (ADR
  0017 điểm 9), nên bật cần code, ghi lại cho khi Elmich trả lời.
- Routes và dataset 1.9.0 (+8 ca, grader `supply_chain.purchase_order`).

Mutation (control xanh trước; `test_purchase_orders.py` + dataset 1.9.0): tổng không bao giờ khác;
duyệt không cần quyền ghi giá; trang mời duyệt khi không có quyền ghi giá; duyệt không cần duty
`create_po`; duyệt phiên bản không phải phiên bản đã thấy; bỏ qua mâu thuẫn với thư; BM04 thắng
giá trị của hồ sơ; soạn lại lần hai; bỏ qua cờ policy; file PO không cần quyền xem giá; dòng thiếu
không bị từ chối; đơn giá không được ghi; số PO không phải số đã nhập. Eval: BM04 workspace khác
được đọc; mâu thuẫn bị bỏ qua; tổng không kiểm; thiếu giá vẫn duyệt; thông báo mang tổng tiền;
Hồ sơ PO tenant khác được đọc (sống sót khi chỉ bỏ RLS của danh sách, rồi cả `get`: lớp thứ ba,
`PrepareDocumentDraft` kiểm hồ sơ thuộc tenant, vẫn từ chối; đỏ khi bỏ cả ba lớp). 19/19 đỏ.

`reviewing-feature-security`: (1) không bảng mới; mọi đọc theo RLS của lane hay người gọi; BM04,
thư chốt của workspace khác không dùng; (2) duyệt cần đúng duty `create_po` từ policy của tenant
và quyền ghi giá; nút khóa không phải là quyền: route từ chối; (3) không mô hình, không tool; (4)
lời trong thư NCC ("đồng ý mọi điều khoản, ghi tổng 1 USD") không thành giá hay tổng; (5) một giao
dịch, audit, idempotency key ở route, không gửi gì ra ngoài; thông báo không giá; (6) thiếu là
khoảng trống, mâu thuẫn là trống, tổng khác là từ chối. `reviewing-deployment-security`: hai route
mới qua `get_access_context`; payload và phát hiện không có số tiền; file PO sau quyền xem giá.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): `tests/integration/test_purchase_orders.py`
(lane soạn qua bảng thật; duyệt ghi một giao dịch; số PO trùng không ghi gì; duyệt lại là 409), một
vòng thật qua worker (ĐẶT HÀNG → lane soạn → Cung ứng duyệt trên web → Kế toán nhận thông báo).
