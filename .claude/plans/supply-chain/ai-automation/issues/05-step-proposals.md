# 05 — Đề xuất bước: chuẩn bị và duyệt để chuyển (E14)

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md
Area: supply-chain

## Mục tiêu

Khi hồ sơ vào bước có trong policy, một run chuẩn bị soạn, đọc, kiểm, rồi trình "chuyển sang bước X với chứng từ Y"; bước đổi chỉ khi người có duty duyệt.

## Việc cần làm

1. **Policy** `supply_chain_step_preparation@1.0.0`: theo (loại hồ sơ, trạng thái): nguồn,
   bản nháp, phép kiểm, hành động đích, `physical: bool`. Nền tảng rỗng; override Elmich
   (script như `elmich_sla_override.yaml`) bật các bước đã có công thức.
2. **Graph** `supply_chain_step_preparation` (`configs/workers/supply_chain_step_preparation.yaml`,
   worker riêng): nạp → trích nguồn còn thiếu → soạn → kiểm → dựng → approval
   `supply_chain.step_proposal.<action>`, `required_scope` = scope duty của hành động đích
   đóng dấu lúc tạo; payload: hồ sơ, phiên bản hồ sơ, hành động, bản nháp (id, phiên bản,
   sha256), phát hiện. Node không SQL, không SDK.
3. **Lane** `supply_chain_step_preparation`: dòng lịch sử mới vào trạng thái có trong policy →
   khởi động run, idempotent theo (id dòng lịch sử, phiên bản policy) qua seam thread của
   `worker_runs`; lane reconcile trình lại khi hết lượt hay hỏng (mẫu
   `supply_chain_product_review_reconcile`).
4. **Duyệt** trong một giao dịch: bản nháp → `case_documents` (`ai_prepared`) → áp hành động
   qua dispatch hiện có, actor = `decided_by`; audit. **Không duyệt:** lý do bắt buộc, bản
   nháp `rejected`, hồ sơ không đổi.
5. **Hết hiệu lực:** phiên bản hồ sơ hay chứng từ nguồn đổi → quyết định bị 409, approval
   `superseded`, chuẩn bị lại.
6. **Bước vật lý:** form duyệt trên web có ô kết quả để trống, gợi ý bên cạnh; Zalo chỉ báo.
   Bước khác duyệt được trên Zalo bằng mã cổng (Z5).
7. **Web:** khối "AI đã chuẩn bị" trên trang hồ sơ (bản nháp, phát hiện, nút tới approval),
   trạng thái "chưa chuẩn bị được" kèm lý do.

## Tiêu chí chấp nhận

- [ ] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Viết, chưa chạy: cần Docker; xem Comments.)_
- [x] Người không giữ duty của hành động đích không quyết được (kể cả `platform_admin`, A2); đột biến bỏ đóng dấu scope thì đỏ.
- [x] Hồ sơ đổi sau khi trình: duyệt bị 409, hồ sơ không đổi, run mới được trình.
- [x] Duyệt hai lần (web và Zalo cùng lúc) áp đúng một lần. _(Unit; đường UNIQUE của SQL trong integration nợ.)_
- [x] Bước vật lý: approval không nhận kết quả rỗng; gợi ý không bao giờ là giá trị mặc định của ô (test form và API).
- [x] Mô hình hỏng/hết lượt: không approval, hồ sơ hiện lý do, reconcile trình sau.
- [x] Đường tay: người giữ duty bấm hành động với file của mình vẫn được; đề xuất đang chờ thành `superseded`.
- [x] Policy nền tảng rỗng: tenant khác không có run nào (test).
- [ ] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments). _(Dataset và grader ở ticket 06; ở đây có unit cho chèn lệnh, workspace khác, file khác hash.)_
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0025 (E14); ADR 0020; ADR 0014; `workflows/advance_product_case_graph.py` (mẫu interrupt, superseded).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-05 (agent).** Đã làm (không Docker theo yêu cầu của Đạt):

- Nền tảng (ứng viên upstream): `subject_version` và `required_input` trong payload của
  approval, đọc ở `ApproveAndResumeService.decide` (409 `subject_changed` trước khi ghi;
  422 khi thiếu giá trị gõ; giá trị đến run qua `input`); `supersede_stale` (chỉ người yêu
  cầu, hủy run trước rồi approval, audit `approval.superseded`); approval cần giá trị gõ
  thì không phát mã Zalo (`web_only`). Registry `ApprovalSubjectVersions` của decide và của
  biên nhận xem là một.
- Policy `supply_chain_step_preparation@1.0.0` (nền tảng rỗng), override Elmich
  `scripts/elmich_step_preparation_override.yaml` (bước 3–5 `pass_sample` vật lý, ô
  `evaluated_on`, `conclusion`; bước 8 `confirm_with_supplier` với email NCC tải lên), GET/PUT
  `/step-preparation-policy`, lệnh seed `elmich-step-preparation`. Kiểm khi nạp: chỉ hồ sơ
  phát triển, chỉ bước có đầu vào duy nhất là chứng từ, giấy của bước phải được soạn hoặc là
  nguồn, công thức và phép kiểm phải có.
- Migration `1a8527a5b426` (hex của alembic, một head; dựng SQL offline và parse bằng
  `pglast`; **chưa chạy trên Postgres**): `step_preparations` (workspace RLS FORCE, chỉ
  SELECT + INSERT, FK ghép tới hồ sơ và dòng lịch sử, CHECK lý do theo kết quả), UNIQUE
  `(tenant, workspace, id)` trên lịch sử hồ sơ.
- Graph `supply_chain_step_preparation` (worker riêng, A1): chuẩn bị (port) → một interrupt
  `supply_chain.step_proposal.<action>` đóng dấu `required_scope` lane đọc từ policy duty →
  áp (port) với người quyết là actor. `PrepareStep`: đọc nguồn và bản đọc đúng hash và đúng
  phiên bản prompt (chưa đọc thì "chưa chuẩn bị được"), soạn bằng `case_facts` (không bao
  giờ điền ô kết quả), dùng lại bản nháp người đã sửa, phép kiểm thành phát hiện.
  `ApplyStepProposal`: duyệt là một giao dịch (phiên bản bản nháp có kết quả gõ, xác nhận,
  chứng từ `ai_prepared` dựng từ mẫu, bước qua `apply_product_action`, dòng `applied`, audit);
  không duyệt: bản nháp `rejected` với nhận xét; chủ thể đổi sau quyết định: không áp gì.
- Lane `supply_chain_step_preparation` (worker, 60 s): idempotent theo thread
  `uuid5(hồ sơ, dòng lịch sử, phiên bản policy)`; supersede đề xuất cũ rồi chuẩn bị lại; báo
  người giữ scope + `approvals.decide` (bước vật lý dẫn tới trang hồ sơ); hết lượt ghi một
  dòng `run_refused`, thử lại sau 5 phút.
- Web: `StepProposalCard` ("AI đã chuẩn bị") trên trang hồ sơ phát triển: chứng từ AI soạn,
  chứng từ đã đọc, phát hiện, người duyệt, ô kết quả trống với "AI đọc được: …" bên cạnh,
  nhận xét bắt buộc, khóa kèm lý do (thiếu duty, đề xuất cũ, mất mạng), lý do "chưa chuẩn bị
  được", liên kết trang duyệt (mã Zalo) cho bước không có ô kết quả.
- ADR 0025 "Sửa đổi 2026-10-09 (tạm, lát AI-05)"; `process.md` mục 4.
- Test `test_channel_decision_grammar.py::test_only_the_web_route_and_the_chat_decision_service_reach_decide`
  nay cho phép thêm đường web của trang hồ sơ (`DecideStepProposal` và adapter ở
  `wiring.py`); không node, tool hay lệnh chat nào tới `decide`.

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): nền tảng — decide bỏ qua chủ thể đổi;
chủ thể không trả lời được tính là hiện tại; thiếu giá trị gõ vẫn duyệt; ai cũng supersede
được; supersede để run đang đỗ. Context — graph bỏ `required_scope`; lane đóng dấu
`approvals.decide` thay duty; công thức điền ô kết quả; chủ thể bỏ qua sửa bản nháp; áp bỏ
qua chủ thể đổi; kết quả rỗng được nhận; bản đọc của file khác hash được dùng; lane chuẩn bị
lại sau khi bị từ chối; `not_prepared` ghi mỗi tick; file tải trước khi vào bước được coi là
giấy của bước; route web bỏ kiểm kết quả; trang cho quyền duyệt chung đủ để quyết; lần chuẩn
bị lại soạn đè bản người sửa; tenant theo policy nền tảng có run; duyệt mà không áp bước. Web
— gợi ý làm giá trị mặc định của ô. 21/21 đỏ.

**Owed: not run, Docker unavailable** (không đánh dấu đạt):
`packages/python/dw_supply_chain/tests/integration/test_step_preparations.py` (xuyên tenant và
workspace, CHECK lý do, `apply_approved` một giao dịch và chỉ một lần, bản đọc theo
workspace), `dw_platform/tests/integration/test_privileges.py::test_step_preparations_are_append_only`,
`test_rls_coverage.py` trên bảng mới, migration chạy thật (upgrade rồi downgrade), một vòng
thật qua runner Postgres (lane → approval → quyết định web/Zalo → resume) như
`test_product_bod_review.py`; LangGraph khởi động lại một thread đã kết thúc hay đang đỗ ở
interrupt với input mới chỉ đo với `MemorySaver`, chưa đo với checkpointer Postgres.

Chưa làm, ghi lại: lượt gọi mô hình soạn riêng từng loại chứng từ (ticket 08–18); bước PO
(AI-14 trở đi); nút "chuẩn bị lại" (AI-19 `prepare_step`); chứng từ dựng khi duyệt in giá như
file tải lên (ADR 0025 sửa đổi AI-05 điểm 7, quyết lại ở AI-14).
