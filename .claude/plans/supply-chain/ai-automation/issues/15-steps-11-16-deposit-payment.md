# 15 — Bước 11 và 16: đề nghị đặt cọc, thanh toán, đối chiếu

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/14-step-10-purchase-order.md
Area: supply-chain

## Mục tiêu

AI soạn đề nghị đặt cọc và thanh toán; đọc PI, hóa đơn, UNC; code đối chiếu số tiền, tiền tệ, người thụ hưởng với PO và danh mục NCC; Kế toán trả tiền ở ngân hàng rồi duyệt.

## Việc cần làm

1. `DocumentType` mới: `proforma_invoice`, `commercial_invoice`, `bank_transfer_receipt`.
2. Mẫu `deposit_request`, `payment_request`; số tiền do code (cọc = tổng × %, cuối = tổng −
   đã cọc).
3. Trích PI, hóa đơn, UNC; số tài khoản che trước mô hình, code so với
   `supplier_bank_accounts` (khác → phát hiện "tài khoản thụ hưởng khác danh mục").
4. Đối chiếu ba chiều PO / hóa đơn / packing list (khi ticket 17 có).
5. Bước vật lý: `confirm_deposit`, `confirm_payment` có ô số tiền đã trả để trống.
6. `po_payments` ghi khi duyệt; `deposit_docs`, `payment_docs` trở thành bắt buộc theo
   policy tenant (QE-02).

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới. Hồ sơ PO tenant khác không soạn, không duyệt; PI, UNC, tài khoản NCC của workspace khác không dùng, cả khi adapter quên RLS: unit và eval. Integration viết, chưa chạy.)_
- [x] Số tài khoản không bao giờ trong request tới gateway. _(Che trước lượt gọi; code giữ digest; unit và eval chèn lệnh đổi tài khoản.)_
- [x] Tài khoản khác danh mục: phát hiện đỏ trên approval; không tự chặn, người quyết.
- [x] Người thiếu `commercial.read` không thấy số tiền. _(Trường giá của bản nháp, gợi ý số tiền, file giấy thanh toán; phát hiện và thông báo không mang số.)_
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- `process.md` hàng 11, 16; ADR 0021 sửa đổi 2026-10-06 (lưu 10 năm).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát AI-15 (agent).** Đã làm (không Docker):

- Lệch ticket (ADR 0025 sửa đổi AI-15): không qua approval run; theo mẫu trang Hồ sơ PO của AI-14,
  khái quát thành `domain.po_step` (bước Hồ sơ PO AI chuẩn bị, bật theo tên ở `po_steps`). Bốn bước:
  `deposit_request` (đã tạo PO → `request_deposit`, Cung ứng), `deposit_payment` (chờ cọc →
  `confirm_deposit`, Kế toán), `final_payment_request` (hàng về cảng → `request_final_payment`),
  `final_payment` (chờ thanh toán → `confirm_payment`). Elmich override 1.8.0 bật cả bốn.
- Chứng từ mới `proforma_invoice`, `commercial_invoice`, `bank_transfer_receipt` (migration
  `8e2d87208f42`); prompt trích `extract_proforma_invoice@1.0.0`, `extract_commercial_invoice@1.0.0`,
  `extract_bank_transfer_receipt@1.0.0`; skill `step_documents@1.1.0`. Code đọc số tài khoản trước khi
  che, giữ digest (ADR 0021 sửa đổi AI-15).
- Mẫu `supply_chain.deposit_request@1.0.0` (loại `deposit_docs`), `supply_chain.payment_request@1.0.0`
  (`payment_docs`): cọc = tổng × %, thanh toán = tổng − cọc đã ghi; tài khoản thụ hưởng từ danh mục.
- Phép kiểm (`domain.payment_check`): tổng, tiền cọc, số UNC với số code tính (tới cent); tiền tệ; tài
  khoản bằng digest; dòng hóa đơn với dòng PO theo SKU (số lượng, đơn giá, SKU ngoài PO, dòng PO thiếu,
  dòng không SKU). Ba chiều PO / hóa đơn / packing list: chiều packing list (và kết quả QC) đến với
  AI-17, nơi packing list được đọc.
- Ô kết quả `paid_amount`, `paid_on` để trống, gợi ý là số UNC đọc được; duyệt ghi `po_payments`
  (trích dẫn giấy đề nghị đã duyệt) cùng giao dịch với bước.
- QE-02: policy `supply_chain_po_documents@1.0.0` (nền tảng không; Elmich override: xác nhận cọc cần
  `deposit_docs`, xác nhận thanh toán cần `payment_docs`), `GET|PUT /po-documents-policy`, lệnh seed
  `elmich-po-documents`; hỏi ở người bấm, apply node của graph duyệt và đề xuất bước.
- Web: thẻ "Bước 11/16 ..." trên trang Hồ sơ PO (phát hiện bằng chữ, đỏ khi tài khoản khác danh mục,
  ô kết quả trống với gợi ý bên cạnh, số tiền ẩn khi thiếu quyền xem giá, duyệt có xác nhận).
- Dataset và routes 1.10.0 (+37 ca: 24 ca trích PI/hóa đơn/UNC: đúng, chèn lệnh đổi tài khoản, xuyên
  tenant, xuyên workspace, thiếu bằng chứng, số bịa, mâu thuẫn, không đọc được; 13 ca bước, grader
  `supply_chain.po_step`: đề nghị cọc, đổi tài khoản trên PI và UNC, PI ghi "tổng 1 USD", hồ sơ tenant
  khác, PI và tài khoản workspace khác, thiếu PI, số đề nghị bị sửa, PI cọc khác, xác nhận cọc, thiếu
  giấy bắt buộc, thiếu số đã chi, đối chiếu hóa đơn, chưa ghi cọc).

Mutation (control xanh trước; `test_po_steps.py`, `test_document_extraction.py`, `test_handlers.py`,
`test_advance_case_graph.py`, `test_case_documents.py` + các ca 1.10.0): tài khoản khác không là khác;
mô hình thấy văn bản chưa che; tài khoản đọc từ văn bản đã che; số trần dài thành tài khoản; số bản
nháp không so; duyệt không cần duty; duyệt không cần quyền ghi giá; đề xuất bỏ giấy bắt buộc; người
bấm bỏ giấy bắt buộc; graph bỏ giấy bắt buộc; gợi ý số tiền hiện khi thiếu quyền; bước tenant chưa bật
vẫn duyệt; giấy cọc tải không cần quyền giá; khoản thanh toán không trích giấy; cọc là cả tổng; thanh
toán bỏ cọc; không so số lượng hóa đơn; không so đơn giá hóa đơn; thông báo mang số tiền; tổng PI khác
không nêu; lane soạn lần hai; lane soạn khi tenant chưa bật; đề nghị lấy tài khoản khác danh mục; bỏ
lọc workspace của code (sống sót lần đầu: RLS của fake giữ; thêm test adapter quên RLS rồi đỏ); duyệt
thiếu số đã chi. 25/25 đỏ.

`reviewing-feature-security`: (1) không bảng mới; mọi đọc theo RLS của lane hay người gọi, lớp thứ hai
lọc tenant và workspace của chứng từ; hồ sơ tenant khác là không thấy; (2) duyệt cần duty của hành động
đích từ policy của tenant và quyền ghi giá, kiểm ở handler; giấy bắt buộc ở cả ba cửa; (3) không agent,
không tool; lane chỉ soạn giấy, không đổi trạng thái; (4) văn bản PI/hóa đơn/UNC là `<input>`, số tài
khoản che trước lượt gọi, digest do code, schema cấm mô hình cài khóa tài khoản; "ghi tổng 1 USD, đổi
tài khoản" không đổi số hay tài khoản của đề nghị; (5) một giao dịch, audit không số tiền hay số tài
khoản; (6) không đọc được, chưa đọc, không có tài khoản danh mục đều là phát hiện, không bao giờ
"khớp". `reviewing-deployment-security`: ba route mới qua `get_access_context`, không secret, không URL
ra ngoài; payload không số tiền ngoài gợi ý đã lọc theo scope.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): `tests/integration/test_po_steps.py`
(tài khoản NCC theo tên dưới RLS, không thấy từ workspace khác; lane soạn đề nghị qua bảng thật; duyệt
ghi một giao dịch, duyệt lại là 409; xác nhận cọc ghi `po_payments` trích `deposit_docs`; CHECK nhận ba
loại mới), migration `8e2d87208f42` chạy thật, `test_case_documents` (CHECK bằng `DocumentType`), một
vòng thật qua worker (PO tạo → lane soạn → Cung ứng duyệt → Kế toán tải UNC → lane trích đọc → Kế toán
xác nhận), và lần đọc PI/UNC thật bằng `luna` (cổng live chạy một lần cuối lát AI-18).

Chưa làm, ghi lại: OCR cho ảnh/PDF quét; so tên người thụ hưởng (chỉ so số tài khoản); nhiều đợt
thanh toán (một cọc, một thanh toán cuối như `PaymentKind`).
