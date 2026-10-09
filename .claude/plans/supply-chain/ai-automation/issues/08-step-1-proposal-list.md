# 08 — Bước 1: đọc danh sách SP đề xuất

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

PIC kéo một danh sách SP đề xuất (xlsx, pdf, ảnh trang catalogue) vào; AI tách thành N bản nháp đề xuất, kiểm trùng, gợi ý Category và mức ưu tiên; PIC duyệt từng dòng.

## Việc cần làm

1. Prompt `extract_proposal_list@1.0.0`: mỗi dòng tên SP, mã đề xuất nếu có, NCC nếu có,
   ảnh tham chiếu, trích dẫn.
2. Code: Category chỉ nhận khóa trong danh sách tenant; kiểm trùng mã đề xuất, mã hàng, SKU
   và danh mục đã nạp (ON-01 khi có); mức ưu tiên là gợi ý có lý do tới khi QE-13 có thang.
3. Bản nháp dùng `proposal_drafts` hiện có hoặc bảng lô; duyệt từng dòng gọi đúng handler
   `propose` (PIC = người duyệt).
4. Web: bảng duyệt lô (antd Table, sửa ô, bỏ dòng).

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; unit có xuyên workspace qua adapter quên RLS; xem Comments.)_
- [x] Dòng trùng mã bị đánh dấu, không tạo hồ sơ thứ hai (DB vẫn là chủ).
- [x] Category ngoài danh sách bị từ chối, không đoán gần đúng.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- `process.md` mục 3.2 hàng 1; Z4b (`product_proposal_understanding`).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-08 (agent).** Đã làm (không Docker):

- Migration `381374b3cb35` (một head; dựng SQL offline, parse bằng `pglast`; **chưa chạy
  trên Postgres**): `proposal_lists` (file ≤ 10 MiB trong PostgreSQL, sha256, CHECK cỡ),
  `proposal_list_readings` (một bản đọc mỗi phiên bản prompt, trạng thái CHECK, các dòng),
  `proposal_list_decisions` (một quyết định mỗi dòng; `proposed` có hồ sơ, `dropped` không);
  workspace RLS FORCE, chỉ SELECT + INSERT; hàng đợi definer
  `proposal_lists_awaiting_reading` chỉ trả id.
- Prompt `extract_proposal_list@1.0.0` (skill `process_part_a@1.1.0`, chỉ thêm
  `applies_to`): mỗi dòng tên, mã đề xuất, NCC, mã hàng, ảnh tham chiếu có trích dẫn, gợi ý
  nhóm và mức ưu tiên kèm lý do; văn bản và danh sách nhóm là biến không tin cậy duy nhất.
- `domain.proposal_list`: trường giữ khi trích dẫn có trong văn bản và giá trị trong trích
  dẫn (dùng `ground` của lane trích xuất), nhóm chỉ giữ khi là khóa của tenant (không gần
  đúng, phát hiện `category_unknown`), mức ưu tiên một trong ba, lý do bị bỏ khi có số không
  có trong file hay giống số tài khoản; phát hiện mã đã có hồ sơ, mã lặp, mã hàng/SKU đã cấp,
  tên trùng hồ sơ (chỉ hỏi đúng các mã và tên danh sách nêu).
- Lane `supply_chain_proposal_lists` (worker, nhịp lane trích xuất, one-call gateway):
  danh sách không thuộc workspace của hàng đợi bị từ chối trước lượt gọi; ảnh hay bản quét
  `unreadable` không gọi; che số tài khoản; sai schema `refused`, hỏng `failed`, hết lượt
  không ghi gì; báo người tải khi đọc xong.
- Route `POST/GET /proposal-lists`, `GET /proposal-lists/{id}`, `POST
.../rows/{i}/proposal` (qua `ProposeProductCase`, PIC là người bấm), `POST
.../rows/{i}/dismissal`; tải, đề xuất, bỏ cần scope của `propose`; đọc cần
  `product_case.read`. Web: trang "Danh sách đề xuất" (tải lên) và trang duyệt từng dòng
  (antd Table, ô sửa được, gợi ý nhóm bên cạnh ô trống và không bao giờ là giá trị mặc định,
  "Đề xuất" / "Bỏ dòng" từng dòng, không có nút cả danh sách); mục nav mới.
- Tác vụ mô hình `extract.proposal_list`; routes policy 1.2.0, dataset
  `supply_chain_preparation@1.2.0` (+10 ca: đúng có dấu, không dấu, chèn lệnh, xuyên tenant,
  xuyên workspace, thiếu bằng chứng, số bịa, trùng mã, nhóm là nhãn chứ không phải khóa, ảnh
  không đọc được). ADR 0025 "Sửa đổi 2026-10-09 (lát AI-07, AI-08)".
- Không có gì để bật trong override chuẩn bị bước của Elmich: bước 1 từ danh sách do PIC tải
  lên, không phải đề xuất bước.

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): lane tin danh sách tenant/workspace
khác; gửi văn bản chưa che; gọi mô hình cho file không đọc được; giữ nhóm ngoài khóa; hiện lý
do có số bịa; mã đã có không thành phát hiện; tên trùng không thành phát hiện; mã lặp không
thành phát hiện; ô mô hình không nói tới thành khoảng trống; hết lượt vẫn ghi dòng; tải lên
không cần duty; nhận mọi loại byte; đề xuất đọc trước khi kiểm quyền; bỏ dòng không cần duty;
dòng đã quyết đề xuất lại được; danh sách của workspace khác với được qua adapter quên RLS
(sống sót lần đầu: fake chỉ "rò" bảng danh sách, không rò bản đọc; sửa fake rồi đỏ). 16/16 đỏ.

`reviewing-feature-security`: (1) RLS FORCE ba bảng, handler kiểm workspace của danh sách
(test với adapter rò), integration nợ; (2) mọi đường ghi kiểm scope của `propose` trước khi
đọc gì, và `propose` kiểm lại; (3) tầm mới: đọc file thành dòng gợi ý, không hồ sơ nào nếu
người không bấm; (4) văn bản và danh sách nhóm là dữ liệu trong `<input>`, ca chèn lệnh, nhóm
do mô hình chọn được giải về khóa thật hay bỏ; (5) audit cùng giao dịch; hồ sơ tạo ra mang
nguồn `proposal_list` trong audit của `propose`; (6) file sống tới khi workspace bị offboard
(chưa có hạn lưu riêng; ghi lại), plan và hạn mức kiểm trước lượt gọi.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_proposal_lists.py` (xuyên tenant và
workspace, UNIQUE bản đọc và quyết định, CHECK quyết định, hàng đợi definer, mã đã cấp theo
workspace), `test_privileges.py::test_ai_drafting_tables_are_append_only` (ba bảng mới),
`test_rls_coverage.py`, migration chạy thật.

Chưa làm, ghi lại: ảnh và bản quét (cần mô hình đọc ảnh, ADR 0021 sửa đổi chưa cho); hạn lưu
file danh sách; thang ưu tiên thật (QE-13); kiểm danh mục đã nạp (ON-01); đề xuất và ghi
quyết định của dòng trong một giao dịch.
