# 09 — Bước 3–5: biên bản đánh giá, phiếu chỉnh sửa, kiểm vòng mẫu

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/04-skills-registry.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

R&D nhập kết quả đo theo danh sách tiêu chí; AI soạn biên bản, đối chiếu tiêu chuẩn của Category, soạn phiếu khi không đạt, và ở vòng sau kiểm từng mục của phiếu.

## Việc cần làm

1. Policy `supply_chain_sample_criteria@1.0.0` (tiêu chí theo Category, ngưỡng số; tenant
   ghi đè); skill `sample_evaluation`.
2. Form checklist của vòng mẫu (giá trị đo, ảnh); code so ngưỡng → phát hiện.
3. Bản nháp `sample_evaluation` (biên bản) và, khi có tiêu chí trượt, `sample_revision_request`
   (phiếu) từ tiêu chí trượt và các vòng trước; cùng bản nháp tin gửi NCC (ticket 07).
4. Vòng sau: mỗi mục phiếu cũ → đã sửa / chưa / không kiểm được.
5. Approval bước vật lý: ô kết quả Đạt / Cần chỉnh sửa / Hủy để trống, gợi ý bên cạnh.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; unit và eval có số đo ở workspace khác; xem Comments.)_
- [x] Kết quả rỗng không duyệt được; gợi ý không điền sẵn.
- [x] Ngưỡng của tenant A không áp cho B.
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- `process.md` mục 3.2 hàng 3–5; ADR 0016.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-09 (agent).** Đã làm (không Docker):

- Policy `supply_chain_sample_criteria@1.0.0` (nền tảng: ba tiêu chí kiểm trung tính;
  tenant ghi đè qua GET/PUT `/sample-criteria-policy`, `action_duties.read|write`); override
  Elmich `scripts/elmich_sample_criteria_override.yaml` (nồi, chảo; ngưỡng đặt chỗ chờ R&D
  Elmich), lệnh seed `elmich-sample-criteria`. Tiêu chí số (đơn vị, min/max) hay kiểm
  (pass/fail); phép so một chỗ (`domain.sample_criteria`).
- Migration `1021f7fe88f0` (một head; dựng SQL offline, parse bằng `pglast`; **chưa chạy
  trên Postgres**): `sample_measurements` (chỉ thêm, workspace RLS FORCE, CHECK khóa tiêu
  chí, giá trị, vòng), mục đích thư `sample_revision_request`.
- Trang hồ sơ: khối "Số đo vòng mẫu" (R&D nhập từng tiêu chí, code hiện Đạt / Không đạt /
  Chưa đo; cần quyền ghi + duty R&D, hồ sơ đang test mẫu). Route `GET
.../sample-checklist`, `POST .../sample-measurements`.
- Chuẩn bị bước 3-5 (override chuẩn bị bước Elmich 1.2.0): công thức `sample_evaluation`
  (bảng tiêu chí của code, ghi chú AI viết có dẫn chứng) và `revision_request` (mỗi tiêu chí
  trượt một mục, yêu cầu AI viết chỉ giữ khi dẫn đúng tiêu chí đó và mọi số có trong bằng
  chứng); phép kiểm `criteria_measured` và `revision_checked` (mục phiếu vòng trước đã sửa /
  chưa / không kiểm được; phiếu vòng trước là file tải lên thì nêu); một lượt gọi
  `draft_sample_evaluation@1.0.0` (skill `sample_evaluation@1.1.0`) chỉ khi có bản nháp mới
  cần lời; mô hình không trả lời được thì bản nháp vẫn có bảng của code.
- Ba kết quả: ô kết luận là lựa chọn (Đạt / Cần chỉnh sửa / Hủy), để trống, gợi ý của code
  bên cạnh (tiêu chí trượt → Cần chỉnh sửa; mọi tiêu chí đạt → Đạt; còn tiêu chí chưa đo →
  không gợi ý); duyệt áp đúng hành động chọn, lý do là nhận xét; phiếu không dùng bị đóng.
  Web: `StepProposalCard` hiện ô chọn khi bước có lựa chọn; `DraftsCard` hiện "AI viết, đã
  kiểm dẫn chứng".
- Chủ thể của đề xuất gồm số đo của vòng; nguồn vừa đọc vừa soạn chỉ tính trong vòng
  (sửa một lỗi của AI-05: vòng 2 đọc biên bản vòng 1 làm báo cáo test).
- Thư gửi NCC cho phiếu đã duyệt (mục đích mới, mẫu thư 1.1.0, prompt thư 1.1.0): đính kèm
  phiếu, mục của phiếu là bằng chứng thư được dẫn.
- Tác vụ `draft.sample_evaluation`; routes policy 1.3.0; dataset
  `supply_chain_preparation@1.3.0` (+11 ca: đúng có dấu, không dấu, chèn lệnh, xuyên tenant,
  xuyên workspace, số đo ở workspace khác, thiếu bằng chứng, số bịa, duyệt Cần chỉnh sửa,
  kết luận ngoài lựa chọn, mô hình sai schema). ADR 0025 "Sửa đổi 2026-10-09 (lát AI-09)".

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): giữ yêu cầu cho tiêu chí đạt (sống
sót với eval vì bảng phiếu chỉ có tiêu chí trượt; thêm unit cho `ground_evaluation` rồi đỏ);
yêu cầu không cần dẫn tiêu chí của nó; đảo phép so ngưỡng; bảng biên bản không theo số đo;
gợi ý bỏ qua phép so của code; lựa chọn ngoài danh sách áp hành động chính; hành động cần lý
do không nhận nhận xét; phiếu không chọn vẫn được xác nhận; số đo nhập sau không đổi chủ thể
(chuẩn bị và chủ thể, hai đột biến); biên bản vòng trước tính là báo cáo vòng này; duty khác
nhau vẫn đóng dấu một; nhập số đo không cần duty R&D; nhập khi hồ sơ không test mẫu; tiêu
chí ngoài nhóm; giá trị tiêu chí không nhận; hồ sơ workspace khác qua adapter rò (sống sót
lần đầu: fake không rò; thêm test với adapter rò rồi đỏ); thư phiếu không đính kèm; vòng sau
không kiểm phiếu cũ. 19/19 đỏ.

`reviewing-feature-security`: (1) RLS FORCE bảng mới, ghi số đo trên hồ sơ workspace khác bị
404 (handler) và bị FK/RLS chặn (integration nợ); ngưỡng theo tenant qua `PolicyOverridePort`
(unit: ngưỡng tenant A không áp cho B); (2) nhập số đo cần quyền ghi + duty của bước test mẫu,
kiểm trước khi đọc; quyết định vẫn là của nền tảng (scope đóng dấu, nhận xét bắt buộc); một
approval chỉ một scope, duty khác nhau thì không trình; (3) tầm mới của AI: viết ghi chú và
yêu cầu, không chọn kết luận, không chọn bước; (4) tên SP, ghi chú, báo cáo là dữ liệu trong
`<input>`, ca chèn lệnh đòi kết luận Đạt; (5) audit cùng giao dịch với số đo, quyết định và
chứng từ; (6) số đo sống theo hồ sơ (cascade).

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_sample_measurements.py` (xuyên tenant
và workspace, CHECK), `test_privileges.py::test_ai_drafting_tables_are_append_only` (bảng
mới), `test_rls_coverage.py`, migration chạy thật; một vòng thật qua runner Postgres (số đo →
đề xuất → chọn Cần chỉnh sửa → phiếu thành chứng từ → thư NCC).

Chưa làm, ghi lại: ảnh của vòng mẫu (dùng chứng từ `sample_photo` hiện có, chưa gắn vào từng
tiêu chí); ngưỡng thật của Elmich; mẫu biên bản riêng có cột "kiểm vòng" (hôm nay nằm ở phát
hiện và ghi chú).
