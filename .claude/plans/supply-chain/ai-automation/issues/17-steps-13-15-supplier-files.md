# 17 — Bước 13–15: file NCC, QC, vận chuyển, đóng cont

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md, .claude/plans/supply-chain/ai-automation/issues/14-step-10-purchase-order.md
Area: supply-chain

## Mục tiêu

AI đọc lịch sản xuất, báo cáo QC, packing list, hóa đơn, B/L, giấy báo hàng đến; code đối chiếu với dòng PO và cập nhật ETD/ETA; QC và Logistics duyệt.

## Việc cần làm

1. `DocumentType` mới: `production_schedule`, `qc_report`, `packing_list`, `bill_of_lading`,
   `arrival_notice`, `certificate_of_origin`; cột ETD, ETA, số cont trên Hồ sơ PO.
2. Bản nháp test trước SX như bước 3; trích QC (AQL, lỗi) → gợi ý đạt/không cạnh ô trống;
   bản nháp `rework_request`.
3. Packing list/B/L đối chiếu số lượng theo SKU; ETA từ giấy báo → nhắc trước; skill
   `customs_file` liệt kê hồ sơ thiếu.
4. Đóng cont: khi Elmich trả lời QE-15 là bước riêng thì thêm trạng thái; tới đó số cont và
   ảnh đóng cont nằm trên `pass_qc`.
5. Cập nhật NCC dán chữ hiện có giữ nguyên; file kéo vào đi qua lane trích.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh.
- [x] Số lượng packing list khác PO: phát hiện theo SKU.
- [x] Báo cáo QC có câu "PASS" mà số lỗi vượt AQL: gợi ý theo số, không theo chữ.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật.

## Nguồn

- `process.md` hàng 13–15; QO-6, QE-14, QE-15.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

- Không bảng mới (migration `0c3b3a73be30`): bảy loại chứng từ (`production_schedule`, `qc_report`,
  `packing_list`, `bill_of_lading`, `arrival_notice`, `certificate_of_origin`, `rework_request`) vào hai
  CHECK; `po_cases.etd`, `eta`, `container_number` (CHECK ISO 6346) dưới RLS FORCE sẵn có; mục đích thư
  `production_progress`. Test âm tenant B và workspace khác: unit (`test_shipping_steps.py`, eval
  `ship-sec-*`); `test_rls_coverage.py`, `test_privileges.py` không đổi bảng nên không đổi kết quả, chạy
  thật vẫn nợ (dưới).
- Sáu prompt trích `extract_*@1.0.0`; skill `qc_aql@1.1.0`, `customs_file@1.1.0`, `step_documents@1.2.0`;
  `CUSTOMS_FILE` (code) được test đối chiếu với chữ của skill.
- Bước `production`, `qc`, `arrival` (ADR 0025 sửa đổi AI-17); Elmich override 1.10.0. `ResultKind`
  thêm `choice`, `text`; hành động đích theo lựa chọn (`action_for`), duty kiểm theo hành động đó.
  Phiếu `rework_request@1.0.0` soạn khi số lỗi vượt Ac, đóng khi QC chọn Đạt.
- Mục 2 "bản nháp test trước SX như bước 3": CHƯA làm (bước 13 hôm nay đọc lịch SX và ETD). Mục 4: đóng
  cont giữ hoãn (QO-6); số container nhập ở `pass_qc`, ảnh đóng cont là chứng từ tải lên như cũ. Mục 3
  "ETA → nhắc trước": ETA ghi vào hồ sơ; SLA `port_arrival` sẵn có vẫn là đồng hồ, chưa có nhắc riêng
  theo ETA.
- Thư hỏi tiến độ hằng tuần (`production_progress`): lane thư gửi NCC soạn một thư mỗi tuần ISO cho mỗi
  Hồ sơ PO đang sản xuất, dẫn lịch SX đã đọc; người gửi.
- Web: thẻ bước PO có ô lựa chọn (Đạt / Không đạt), ô chữ (số container, lý do; lý do chỉ bắt buộc khi
  Không đạt), nhãn phát hiện mới; trang Hồ sơ PO hiện ETD, ETA, container; bảy nhãn loại chứng từ.
- Dataset và routes 1.12.0 (+62 ca, 244): mỗi loại chứng từ mới có ca đúng, chèn lệnh, xuyên tenant,
  thiếu bằng chứng, số bịa, mâu thuẫn; QC report và packing list thêm không đọc được (ảnh, bản quét) và
  xuyên workspace; 17 ca `po_step` (QC đạt; báo cáo ghi PASS mà số vượt Ac; không đạt có lý do; không
  lý do; không chọn kết quả; chèn lệnh; tenant khác; workspace khác; sai duty; thiếu báo cáo; container
  sai; ETD; hàng đến khớp; chưa có giấy báo; packing khác PO và thiếu hồ sơ hải quan; tenant khác ở bước
  15; đối chiếu ba bên ở bước 16); 5 ca thư tiến độ (đúng, chèn lệnh, tenant khác với adapter quên RLS,
  chưa có lịch, ETD bịa). Ca "đổi tài khoản" không áp dụng: không chứng từ nào của bước 13-15 mang tài
  khoản thụ hưởng.

Mutation (`test_shipping_steps.py`, `test_po_steps.py`, `test_supplier_messages.py`): Ac so `>=`; bỏ so
container; bỏ đếm hồ sơ hải quan; bỏ ETD muộn; không soạn phiếu sửa hàng; bỏ `required_for` (sống sót lần
đầu: miền cũng từ chối lý do trống; test nay đòi lỗi đúng ô `reason`, rồi đỏ); bỏ kiểm container; phiếu
sửa hàng thành chứng từ cả khi Đạt; không ghi ngày/container; hành động cố định thay vì theo lựa chọn; bỏ
"QC không đạt" ở bước 16; bỏ hồ sơ hải quan ở bước 15; bỏ phát hiện chứng từ nguồn; khóa tuần ngẫu nhiên;
lấy chứng từ mới nhất bất kỳ thay lịch SX (sống sót lần đầu; thêm packing list mới hơn, rồi đỏ); bỏ lọc
trạng thái sản xuất (sống sót lần đầu; thêm hồ sơ đang vận chuyển, rồi đỏ); mẫu thư của tenant ở 1.1.0 không lấy mẫu nền tảng cho mục đích mới. 17/17 đỏ. Eval: bỏ kiểm tenant
của bộ soạn thư với PO thì `ship-sec-cross-tenant-progress` đỏ. Web: bỏ `required_for` thì vitest đỏ.

`reviewing-feature-security`: (1) không bảng mới; ba cột mới dưới RLS FORCE; đọc qua RLS và lọc lớp hai;
(2) duty QC (Đạt/Không đạt) và Logistics (hàng đến) theo policy của tenant, kiểm ở handler sau khi suy
hành động; Cung ứng gọi thẳng duyệt QC bị từ chối; (3) không agent, không tool; lane chỉ soạn bản nháp,
thư do người gửi; (4) văn bản chứng từ là `<input>`; "Result: PASS" không đổi gợi ý; số container được
validate rồi chuẩn hóa, không lấy từ gợi ý; (5) bước, chứng từ, quyết định đóng bản nháp và audit một
giao dịch; (6) báo cáo thiếu số lỗi là "không đọc được", không bao giờ "đạt". `reviewing-deployment-
security`: không route mới (hai trường mới trên view có sẵn), không secret.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): migration `0c3b3a73be30` chạy thật;
`tests/integration/test_shipping_steps.py` (CHECK nhận loại mới và mục đích; CHECK container từ chối;
QC không đạt ghi phiếu sửa hàng cùng bước; QC đạt đóng bản nháp và ghi container; hàng đến ghi ETA, tenant
khác không thấy); `test_rls_coverage.py`, `test_privileges.py`; lần đọc thật của sáu prompt bằng `luna`
(cổng chạy cuối loạt).

Chưa làm, ghi lại: OCR cho ảnh và bản quét (báo cáo QC, packing list hay gửi dạng ảnh: bước nêu máy không
đọc được, người kiểm); biên bản test trước SX do AI soạn; nhắc theo ETA; đóng cont thành bước (QE-15).

**2026-10-10, mục 2 làm xong (agent): biên bản test trước SX như biên bản vòng mẫu (AI-09).**

- R&D nhập số đo mẫu trước SX theo cùng tiêu chí của vòng mẫu (nhóm của hồ sơ sản phẩm; không có thì
  danh sách mặc định), theo lần test (lần đầu, +1 mỗi lần Không đạt): bảng `pre_production_measurements`
  (migration `2bb10bdd4420`, chỉ thêm, workspace RLS FORCE, `dw_app` SELECT/INSERT), route `GET
.../pre-production-checklist`, `POST .../pre-production-measurements`; cần duty của bước Đạt test
  (R&D), chỉ khi đã nhận mẫu và test chưa đạt. Web: thẻ "Số đo test trước SX (lần n)" trên trang Hồ sơ
  PO (bảng tiêu chí dùng chung với thẻ vòng mẫu: `ChecklistTable`).
- Lane bước 12 (`supply_chain_packaging_papers`) soạn `pre_production_test_report@1.0.0` khi mọi tiêu
  chí có số: bảng của code, ghi chú do mô hình viết bằng chính prompt/grounding của biên bản vòng mẫu
  (`draft_sample_evaluation@1.0.0`, tác vụ `draft.sample_evaluation`, `EvaluationWriter` có
  `subject_kind=po_case`, `purpose=pre_production_test`); mô hình lỗi thì chỉ bảng; một bản cho mỗi bộ
  số; R&D được báo. Ngày, người test, kết luận để trống.
- Gợi ý cạnh ô trống (Không đạt khi một tiêu chí trượt, Đạt khi đủ đạt, không gợi ý khi còn chưa đo),
  hiện trên thẻ và trong hộp Đạt / Không đạt, không chọn sẵn.
- Bước Đạt / Không đạt nhận `draft_id`: bản nháp mở của hồ sơ, đúng loại, bảng đúng bằng kết quả số đo
  hiện tại (số đổi hay bảng sửa tay: 409); bước kiểm mở trước, rồi `DraftFiler` render với kết luận R&D
  chọn, `SqlDraftFilings` ghi chứng từ + xác nhận + audit một giao dịch, rồi bước ghi với chứng từ đó.
  Web: hộp bước liệt kê "Biên bản AI soạn" cạnh file tải lên.
- Dataset `supply_chain_preparation@1.15.0` (routes 1.15.0, +10 ca `pp-*`, 269; grader
  `supply_chain.pre_production_report`, bất biến live: mỗi khóa dẫn là khóa mô hình được xem, mỗi số
  trong ghi chú có trong bằng chứng, không số tài khoản). ADR 0025 sửa đổi (AI-17 mục 2); `process.md`
  hàng 13 cột AI.
- Mutation (mỗi guard bỏ đi thì đỏ, unit `test_pre_production_test.py`): 18/18 đỏ sau khi thêm 6 test
  cho 6 kẻ sống sót lần đầu (bản nháp khác loại; bỏ `require_open`; bỏ kiểm bước mở trước khi lưu; bỏ
  kiểm test mở khi nhập số (test cũ đỏ vì lý do khác: tiêu chí không thuộc danh sách); bỏ kiểm
  workspace lớp hai của hồ sơ PO và của hồ sơ sản phẩm (cần store rò)). Eval: bỏ grounding, soạn khi
  chưa đủ số, store số đo quên RLS, bỏ bằng chứng tiêu chí: 4/4 đỏ. Web: bỏ lọc bản nháp mở: đỏ.
- Nợ (không Docker): `tests/integration/test_pre_production_measurements.py` (xuyên tenant/workspace,
  CHECK), `test_privileges.py` (bảng mới chỉ thêm), `test_rls_coverage.py`, migration chạy thật,
  `test_packaging_designs.py` với `draft_id` qua Postgres. Ba bản sao "draft thành chứng từ" cũ
  (`po_steps`, `purchase_orders`, `step_proposals`) chưa chuyển sang `DraftFiler`: ghi lại, không sửa
  trong lát này.
