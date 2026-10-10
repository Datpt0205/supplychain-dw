# 18 — Bước 17: phiếu nhập kho và đối chiếu số đếm

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/17-steps-13-15-supplier-files.md
Area: supply-chain

## Mục tiêu

AI soạn phiếu nhập kho từ dòng PO và packing list; Kho nhập số đếm; code đối chiếu, soạn biên bản chênh lệch và thư khiếu nại NCC.

## Việc cần làm

1. `DocumentType` mới `warehouse_receipt`, `discrepancy_report`; số đếm theo dòng
   (`po_case_line_receipts`, chỉ thêm).
2. Bước vật lý: `complete` có ô số đếm để trống, gợi ý là số giao.
3. Chênh lệch → bản nháp biên bản và tin gửi NCC (ticket 07).

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [x] Số đếm rỗng không duyệt được.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 17; HR3.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

- Migration `e0a7cb26a3bf`: bảng `po_case_line_receipts` (chỉ thêm; RLS ENABLE + FORCE, chính sách
  một dạng; `dw_app` SELECT, INSERT; FK tổng hợp tới dòng PO `ON DELETE CASCADE` và tới phiếu nhập kho
  `NO ACTION`; CHECK số đếm 0..10.000.000, version ≥ 1; UNIQUE theo dòng và version; mọi tên dưới 63
  ký tự); loại chứng từ `warehouse_receipt`, `discrepancy_report`; mục đích thư `discrepancy_claim`.
- `domain.receipt_check`: `counted_lines` (mọi dòng PO có số, không số lạ, trong khoảng), `discrepancies`
  (một câu trả lời cho audit, biên bản, thư). Bước `warehouse` (ADR 0025 sửa đổi AI-18), mẫu
  `warehouse_receipt@1.0.0`, `discrepancy_report@1.0.0`; Elmich override 1.11.0. Mẫu thư 1.3.0
  (`PURPOSES_ADDED_AFTER` cho 1.2.0), prompt `draft_supplier_message@1.3.0`.
- Web: thẻ bước PO có danh sách dòng cần đếm (ô số trống, "NCC ghi đã giao" bên cạnh, khóa tới khi đủ
  mọi dòng); nhãn loại chứng từ và mục đích thư mới.
- Dataset và routes 1.13.0 (+15 ca, 259): 10 ca `po_step` (đúng; đếm thiếu → một biên bản; thiếu số
  một dòng; không nhập số; sai duty; tenant khác; packing list workspace khác; packing list chèn lệnh;
  packing khác PO nhưng đếm khớp số giao; số giao trong phiếu nháp bị sửa không đổi đối chiếu); 5 ca thư
  khiếu nại (đúng; mô hình đòi chuyển tiền tới tài khoản mới — đoạn bị bỏ; tenant khác với adapter quên
  RLS; đếm khớp nên không thư; số thiếu bịa). Không có bộ đọc mới: packing list đã có ca ở AI-17.

Mutation (`test_warehouse_steps.py`, `test_po_steps.py`, `test_supplier_messages.py`,
`test_supplier_message_policy.py`): bỏ kiểm thiếu số, kiểm khoảng, kiểm số lạ; không so đếm với giao; so
với số đặt thay vì số giao; bước khác nhận số đếm; không ghi số đếm; số đếm không trích phiếu; soạn lại
biên bản; bỏ kiểm workspace của lane (sống sót lần đầu: kho giả lọc sẵn; thêm cờ `leaky` cho kho PO giả,
rồi đỏ); cửa sổ 30 ngày; phiếu nộp không mang số đếm; phiếu nháp không có số giao; lane không chạy biên
bản; khóa thư ngẫu nhiên; thư khiếu nại khi tenant chưa bật (sống sót lần đầu; thêm test, rồi đỏ); mẫu thư
1.2.0 không lấy mẫu nền tảng (sống sót lần đầu; thêm test, rồi đỏ). 17/17 đỏ. Eval: bỏ kiểm thiếu số thì
ba ca `whs-*` đỏ. Web: bỏ khóa khi còn dòng trống thì vitest đỏ. `count_findings` viết rồi bỏ: không ai
đọc (failure-modes #1); `discrepancies` là câu trả lời duy nhất.

`reviewing-feature-security`: (1) bảng mới có RLS ENABLE + FORCE và chính sách workspace; đọc qua RLS và
lọc workspace lớp hai trong lane (test với adapter quên RLS); (2) duty `warehouse` theo policy của tenant
kiểm ở handler; Cung ứng gọi thẳng bị từ chối; (3) không agent, không tool; lane chỉ soạn bản nháp, thư do
người gửi; (4) packing list là dữ liệu: dòng chèn lệnh là "SKU ngoài PO", không điền số đếm; thư không giữ
đoạn có số tài khoản hay số không có trong số đếm; (5) bước, phiếu, số đếm, audit một giao dịch; số đếm
trích phiếu bằng FK `NO ACTION`; (6) số đếm thiếu là từ chối, không bao giờ "bằng số giao".
`reviewing-deployment-security`: route có sẵn, thân yêu cầu thêm `counts` (tối đa 500 dòng, số nguyên
có giới hạn), không secret.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): migration `e0a7cb26a3bf` chạy thật;
`tests/integration/test_warehouse_steps.py` (ghi cùng bước và trích phiếu; RLS tenant khác và workspace
khác đọc rỗng; CHECK và UNIQUE từ chối; CHECK nhận loại mới); `test_privileges.py` (bảng chỉ thêm);
`test_rls_coverage.py` (bảng mới qua catalog).

Chưa làm, ghi lại: đếm lại (version 2) chưa có màn; biên bản thành chứng từ trực tiếp (hôm nay bản nháp
xem trước, sửa, tải về); ảnh và bản quét packing list vẫn không đọc được (chưa có OCR): số giao để trống
và số đếm so với số đặt.
