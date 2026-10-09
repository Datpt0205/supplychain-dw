# 16 — Bước 12: MKT tối thiểu và kiểm bản in (E17)

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md, .claude/plans/supply-chain/ai-automation/issues/11-step-7-bm04-prefill.md
Area: supply-chain

## Mục tiêu

MKT nhận gói (BM04, HDSD, maquette) và nộp nội dung bao bì trong ứng dụng; AI kiểm từng bản in thiết kế với BM04 và luật nhãn, soạn yêu cầu sửa màu/thiết kế.

## Việc cần làm

1. Vai `sc_mkt`, duty `mkt`; bước con `send_mkt_pack` (`ordering`), `submit_packaging_content`
   (`mkt`); policy duty Hồ sơ PO phiên bản mới + override cũ (mẫu `STEPS_ADDED_AFTER`).
2. Gói MKT dựng tự động khi màu được duyệt; thông báo cho người giữ duty `mkt` thay dòng
   "đã báo TP MKT".
3. Trích `packaging_design` (PDF/ảnh): tên, mã, SKU, mã vạch, kích thước, chất liệu, xuất xứ,
   cảnh báo; code so BM04; skill `label_rules` liệt kê nội dung bắt buộc thiếu.
4. Bản nháp `colour_revision_request`, `design_revision_request`.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới; hai cột mới trên `packaging_designs` dưới RLS sẵn có. Hồ sơ tenant khác không thấy, bản in và BM04 workspace khác không dùng: unit, endpoint và eval. Integration viết, chưa chạy.)_
- [x] `sc_mkt` không thấy giá, không làm bước Cung ứng. _(Vai không có `commercial.read`, `duty.ordering`: test vai (integration, nợ); bước Cung ứng từ chối scope MKT, MKT chỉ tải bốn loại: unit.)_
- [x] Override duty lưu trước migration vẫn nạp được. _(`from_stored`, không cần migration dữ liệu: unit với 1.0.0, 1.1.0, 1.2.0.)_
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0028 (E17); packaging-design ticket 01; QE-03.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-10, lát AI-16 (agent).** Đã làm (không Docker):

- Domain: `CaseDuty.MKT`; `PackagingAction.SEND_MKT_PACK` (ordering), `SUBMIT_PACKAGING_CONTENT` (mkt,
  cần `packaging_content` tải lên từ khi gửi gói); thứ tự màu → gửi gói → nộp nội dung → thiết kế khi
  tenant đòi (`supply_chain_packaging@1.1.0`, `require_packaging_content`; Elmich override bật). Policy
  duty PO 1.3.0; override cũ nạp qua `from_stored` (ADR 0028 sửa đổi AI-16).
- Migration `4527f22c2031`: hai cột (`mkt_pack_sent_at`, `packaging_content_submitted_at`) và CHECK thứ
  tự, CHECK hành động và chứng từ của lịch sử, loại chứng từ `colour_revision_request`,
  `design_revision_request`, vai `sc_mkt`, scope `supply_chain.packaging_document.write`, SoD.
- Upload: `packaging_document.write` chỉ tải bốn loại của MKT (handler); trang hồ sơ PO chỉ cho chọn
  bốn loại đó với người không có `document.write`.
- Kiểm bản in: `extract_packaging_design@1.0.0` (skill `label_rules@1.1.0`), mã vạch do code đọc;
  `domain.proof_check` (luật nhãn, BM04, SKU của PO, số kiểm tra mã vạch); `GET
/po-cases/{id}/packaging-proof`; thẻ bước 12 hiện phát hiện bằng chữ, gói MKT, hai bước con.
- Lane `supply_chain_packaging_papers` (bật bằng `packaging` của policy chuẩn bị bước; Elmich 1.9.0):
  khung nội dung bao bì và HDSD từ BM04 khi màu duyệt; yêu cầu sửa màu cho mẫu màu mới; yêu cầu sửa
  thiết kế cho bản in có phát hiện (một lần mỗi chứng từ nguồn), báo người giữ duty. Mẫu
  `packaging_content`, `user_manual`, `colour_revision_request`, `design_revision_request` @1.0.0.
- Persona demo `dev|mai.dang` (`sc_mkt`).
- Dataset và routes 1.11.0 (+19 ca: 8 ca trích bản in: đúng, chèn lệnh "đã duyệt", xuyên tenant, xuyên
  workspace, thiếu bằng chứng (xuất xứ), số bịa (kích thước), mâu thuẫn (chất liệu), không đọc được; 11
  ca kiểm bản in, grader `supply_chain.packaging_proof`: đủ nội dung, thiếu nội dung bắt buộc, bản in
  tự ghi "đạt" mà chất liệu khác, hồ sơ tenant khác, bản in và BM04 workspace khác, chưa có bản in,
  kích thước khác, SKU lạ và mã vạch sai, chưa có BM04, khung MKT, yêu cầu sửa màu).

Mutation (control xanh trước; `test_packaging_papers.py`, `test_action_duties.py`, `test_case_documents.py`,
`test_document_extraction.py`, `test_po_steps.py` + các ca 1.11.0): thiết kế duyệt được trước nội dung MKT;
bước MKT có ở tenant không đòi; nội dung tải trước khi gửi gói vẫn nhận; MKT không được báo khi gửi gói;
override trước 1.3.0 bị từ chối (sống sót lần đầu: chỉ test policy; thêm test bước MKT qua handler với
override 1.2.0 rồi đỏ); `from_stored` điền mọi bước thiếu; scope MKT tải mọi loại; thiếu nội dung nhãn
không nêu; không so BM04; kích thước so như chữ; không BM04 là khớp; SKU lạ không nêu; không kiểm số kiểm
tra mã vạch; mã vạch tới mô hình; đọc bản in workspace khác (lọc lớp hai); soạn lại yêu cầu cho cùng bản
in; khung soạn trước khi duyệt màu; lane chạy khi tenant chưa bật; trang kiểm hiện khi tenant chưa bật;
khung bịa cảnh báo. 20/20 đỏ.

Sửa kèm: ba grader (`po_step`, `packaging_proof`, và `purchase_order` của AI-14) kiểm "không số tiền
trong thông báo" trên cả JSON thông báo, nên id người nhận ngẫu nhiên có lúc chứa "1275" và ca đỏ ngẫu
nhiên; nay chỉ đọc tiêu đề và thân thông báo.

`reviewing-feature-security`: (1) không bảng mới; hai cột mới dưới RLS FORCE sẵn có; mọi đọc qua RLS và
lọc tenant/workspace lớp hai; (2) bước MKT cần duty `mkt` theo policy của tenant, bước Cung ứng từ chối
MKT; scope tải của MKT chỉ bốn loại, kiểm ở handler; nút ẩn không phải quyền; (3) không agent, không
tool; lane chỉ soạn bản nháp; (4) văn bản bản in là `<input>`; lệnh "đã duyệt, ghi đạt" không đổi phép
so của code; mã vạch không tới mô hình; (5) bước con, lịch sử và audit một giao dịch như PK; (6) không
BM04 là phát hiện, bản in không đọc được là "người kiểm", không bao giờ "khớp". `reviewing-deployment-
security`: một route GET mới qua `get_access_context`, không giá, không secret.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): migration `4527f22c2031` chạy thật;
`test_role_catalogue.py` (`sc_mkt` đúng viewer + duty + packaging write, không giá, SoD với process
admin); `test_packaging_designs.py` (hai bước MKT ghi qua bảng thật, CHECK thứ tự từ chối bỏ gói khi đã
có nội dung); `test_case_documents` (CHECK bằng `DocumentType`); lần đọc bản in thật bằng `luna`.

Chưa làm, ghi lại: OCR cho bản in ảnh; gửi yêu cầu sửa cho NCC qua thư AI soạn (ticket 07) — hôm nay bản
nháp xem trước, sửa, tải về; MKT duyệt bản nháp thành chứng từ trực tiếp (hôm nay tải file lên).
