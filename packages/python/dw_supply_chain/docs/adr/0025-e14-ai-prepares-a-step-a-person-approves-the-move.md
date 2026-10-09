---
status: Accepted (Đạt, 2026-10-09)
date: 2026-10-09
source:
    - ../../../../../docs/products/elmich/process.md#32-bảng-17-bước
    - ../../../../../docs/products/elmich/poc-slides-brief.md # slide 4, 8: bản nháp chứng từ
    - ../../../dw_agent_runtime/src/dw_agent_runtime/adapters/agent_factory.py # build_agent, chưa ai dùng
    - ../../../dw_agent_runtime/src/dw_agent_runtime/contracts.py # ToolDefinition
    - ../../../../../.claude/plans/supply-chain/ai-automation/spec.md
---

# E14. AI chuẩn bị bước, người duyệt việc chuyển bước

Hướng của Đạt ngày 9/10/2026: "việc gì AI làm được thì để AI làm, người chỉ kiểm và
duyệt". Hôm nay AI chỉ đọc tin chat (năm prompt một lượt gọi); mọi chứng từ của 17 bước do
người viết ngoài ứng dụng rồi tải lên như file đóng kín, và slide 4, 8 của PoC hứa bản
nháp phiếu chỉnh sửa, tờ trình, BM04, email chốt NCC, PO mà không code nào làm.

**Quyết định:**

1. **Một lượt chuẩn bị mỗi lần vào bước.** Khi một hồ sơ vào trạng thái mà policy tenant
   `supply_chain_step_preparation` liệt kê, lane `supply_chain_step_preparation` khởi động
   một run của graph `supply_chain_step_preparation` (idempotent theo id dòng lịch sử và
   phiên bản policy). Run đọc hồ sơ, lịch sử, chứng từ qua port; trích xuất chứng từ nguồn
   chưa trích ([ADR 0021](0021-e11-case-documents-through-an-object-storage-port.md) sửa
   đổi 2026-10-09); soạn các bản nháp bước cần; chạy các phép kiểm của bước; dựng file từ
   mẫu; rồi trình **một approval**: "chuyển sang bước X với chứng từ Y".
2. **Mô hình đọc và viết, code quyết.** Mỗi trích xuất, mỗi bản nháp là một lượt gọi có
   cấu trúc, kiểm vào schema Pydantic theo loại chứng từ. Số tiền, tổng, số lượng, hạn do
   code tính. Phép kiểm (BM04 với thư trả lời của NCC, PO với PI, hóa đơn với PO và
   packing list, đếm với giao, tài khoản với danh mục NCC) là code so giá trị có kiểu. Ô
   thiếu hoặc mâu thuẫn thành "khoảng trống" có tên, không bao giờ đoán.
3. **Approval của đề xuất bước:** loại `supply_chain.step_proposal.<action>`;
   `required_scope` là scope duty của hành động đích, đọc từ policy duty lúc tạo và đóng
   dấu ([ADR 0020](../../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md));
   payload mang id hồ sơ, phiên bản hồ sơ, hành động đích, id + phiên bản + sha256 của
   từng bản nháp, danh sách phát hiện. Người yêu cầu là lane (audit như chính nó,
   [ADR 0011](../../../../../docs/adr/0011-a-background-lane-audits-as-itself.md)).
4. **Duyệt là một giao dịch:** mỗi bản nháp thành một dòng `case_documents`
   (`origin = ai_prepared`, `draft_id`), rồi hành động được áp qua đúng bảng dispatch mà
   một cú bấm dùng, actor là người quyết. **Không duyệt:** bản nháp ghi "bị từ chối" kèm lý
   do bắt buộc; hồ sơ không đổi. Bản nháp không bao giờ thỏa điều kiện chứng từ của một
   bước khi chưa được duyệt.
5. **Bước có việc vật lý** (test mẫu, test trước SX, QC, trả tiền, đếm hàng): run chuẩn bị
   hồ sơ với gợi ý đặt **cạnh** ô kết quả để trống; người nhập kết quả rồi duyệt, trên web.
   Zalo chỉ báo, vì mã một lần không mang được kết quả. Đề xuất không có ô kết quả duyệt
   được trên Zalo bằng mã của cổng ([ADR 0014](../../../../../docs/adr/0014-e4-decisions-on-zalo-after-a-portal-view.md)).
6. **Đề xuất cũ thì hết hiệu lực.** Hồ sơ đổi phiên bản hoặc một chứng từ nguồn có bản mới
   sau khi đề xuất được trình: quyết định bị từ chối có tên (409), approval kết thúc
   `superseded`, lane chuẩn bị lại.
7. **Đường tay vẫn còn.** Người giữ duty vẫn bấm được hành động với file của mình như hôm
   nay. Mô hình hỏng, hết lượt chạy, file không đọc được: hồ sơ hiện "chưa chuẩn bị được",
   không đề xuất, không đoán; lane reconcile trình lại khi có lượt.
8. **Không agent nào đổi trạng thái hay gửi ra ngoài.** Vòng lặp agent duy nhất là trợ lý
   hồ sơ chỉ đọc (`build_agent`, autonomy A1, toolset `supply_chain_assistant`, mọi tool
   `side_effect_level: none` trừ `prepare_step` là `internal`, chỉ xin chuẩn bị lại).
9. **Policy:** `supply_chain_step_preparation@1.0.0`, theo (loại hồ sơ, trạng thái): nguồn,
   bản nháp, phép kiểm, hành động đích, có ô kết quả hay không. Nền tảng: rỗng (không chuẩn
   bị gì). Elmich ghi đè: mọi bước. Prompt, mẫu chứng từ, skill đi qua `TenantOverlay`.
10. **Mô hình:** profile `luna` cho soạn và đọc file; một tác vụ chuyển sang Qwen chỉ khi
    qua cổng eval của chính tác vụ đó (`supply_chain_preparation`).

## Phương án đã cân nhắc

- **Một agent có tool ghi cho cả 17 bước.** Bác: một super-agent, khó eval, và một tool
  đổi trạng thái là quyền quyết trong tay mô hình.
- **AI tự chuyển bước khi tin cậy cao.** Bác (Đạt): người duyệt mọi lần chuyển bước.
- **Bản nháp là `case_documents` có cờ.** Bác: `case_documents` chỉ thêm, `dw_app` không
  UPDATE, và một cờ trên bảng mà cổng chứng từ đọc là chỗ fail-open (failure-modes #7).

## Hệ quả

- Bảng mới `document_drafts` (+ quyết định), `document_extractions`; mẫu chứng từ có phiên
  bản; skill registry ở nền tảng; ticket `.claude/plans/supply-chain/ai-automation/`.
- `process.md` mục 4 có cột AI; mỗi bước nói AI chuẩn bị gì.
- Đo được: tỷ lệ bản nháp duyệt nguyên, sửa, từ chối (AI-20).

## Sửa đổi 2026-10-09 (tạm, lát AI-03; bản nháp và mẫu chứng từ)

1. **Dựng trong tiến trình, sau một port.** `DocumentRendererPort` và registry mẫu
   (`dw_agent_runtime.doc_templates`, ứng viên upstream) có một adapter python-docx
   (`adapters.docx_templates`), không qua sandbox docgen: docgen là một shell, không phải
   trình dựng mẫu, và cần Docker. Adapter docgen sau này thay adapter này mà không đổi bên
   gọi. Chưa ra PDF (cần LibreOffice; để adapter docgen).
2. **Không lưu file dựng của bản nháp.** Xem trước dựng lại từ trường và mẫu đã ghim ở mỗi
   lần tải (`GET /drafts/{id}/file`), nên không có loại đối tượng mồ côi mới (failure-modes
   #6) và bản xem trước luôn khớp trường. `document_drafts` không có `object_key`; thay
   vào đó `content_sha256` (loại + mẫu + trường, JSON chuẩn) là thứ approval của ticket 05
   gắn vào. Khi người duyệt, ticket 05 dựng file và lưu thành `case_documents`
   (`origin = ai_prepared`, `draft_id`).
3. **Override mẫu của tenant lưu trong PostgreSQL** (`supply_chain.doc_template_overrides`:
   khai báo + DOCX ≤ 5 MiB, chỉ thêm), không trong bucket: không phải lo mồ côi, offboarding
   theo tenant như `platform.policy_overrides` (cascade từ `platform.tenants`), RLS theo
   tenant (mẫu của công ty dùng cho mọi workspace). Tải lên bằng `PUT /doc-templates`
   (`action_duties.write`), phải thay một mẫu nền tảng cùng mã và cùng loại chứng từ, kiểm
   như file nền tảng (trường khai báo đúng bằng placeholder, không macro).
4. **Loại chứng từ `bod_submission`** (tờ trình BGĐ) thêm vào `DocumentType` để bản nháp
   tờ trình có đích. Mẫu nào cho loại nào: `DRAFT_TEMPLATES` trong domain (một chủ); policy
   chuẩn bị bước (ticket 05) sẽ ghim phiên bản.
5. **Quyết định trên bản nháp:** từ chối (lý do bắt buộc) có ở lát này; duyệt chỉ qua
   approval của bước (ticket 05), trong giao dịch thêm chứng từ. Sửa trường tạo phiên bản
   mới; phiên bản cũ đọc được, trạng thái `superseded`.

## Sửa đổi 2026-10-09 (tạm, lát AI-05; đề xuất bước)

1. **Hai khóa payload nền tảng** (ứng viên upstream, `dw_platform.domain.approval`):
   `subject_version` (phiên bản chủ thể lúc trình; `ApproveAndResumeService.decide` từ chối
   409 `subject_changed` trước khi ghi gì nếu phiên bản hiện tại khác, hay không ai trả lời
   được) và `required_input` (giá trị người duyệt phải gõ; duyệt thiếu bị 422, giá trị đến
   run qua `input` của resume, không mã Zalo nào mang được nên loại đó chỉ duyệt trên web).
   `supersede_stale`: chỉ người yêu cầu (lane) kết thúc approval của mình khi chủ thể đã
   đổi: hủy run đang chờ trước, rồi approval (`cancelled`, audit `approval.superseded`).
   Nền tảng không có trạng thái `superseded`: "superseded" là `cancelled` + audit + dòng
   `step_preparations`, không thêm giá trị CHECK cho bảng approval.
2. **Chủ thể của đề xuất** = phiên bản hồ sơ + từng bản nháp còn là phiên bản mở mới nhất
   với cùng `content_sha256` + chứng từ mới nhất của từng loại nguồn. Sửa bản nháp, tải
   chứng từ nguồn mới, hay bấm bước bằng tay đều đổi chủ thể. Lần chuẩn bị lại của cùng
   dòng lịch sử dùng lại bản nháp đã sửa (lineage ghi trong `step_preparations`).
3. **`step_preparations`** (mới): một dòng mỗi thay đổi (`proposed | not_prepared |
superseded | rejected | applied`), khóa theo (dòng lịch sử, phiên bản policy). Quyết
   định vẫn ở `approval_requests`; dòng này là hệ quả, cho trang hồ sơ và lane. Lane chỉ
   ghi `not_prepared` khi lý do đổi, thử lại sau 5 phút; không chuẩn bị lại sau
   `rejected` hay `applied`.
4. **Approval là strict** (`supply_chain.step_proposal.` trong `strict_approval_prefixes`):
   quyết định nào cũng cần nhận xét; người yêu cầu là lane nên không ai bị chặn vì tự duyệt.
   Không duyệt: nhận xét là lý do từ chối bản nháp.
5. **Chưa có lượt gọi mô hình riêng của run chuẩn bị.** Công thức đầu (`case_facts`) điền
   bản nháp bằng code từ dữ liệu hồ sơ và trường có trích dẫn mà lane trích xuất (AI-02) đã
   đọc; gợi ý cạnh ô kết quả cũng từ đó. Nguồn chưa đọc thì "chưa chuẩn bị được", không gọi
   mô hình lần hai. Prompt soạn riêng từng loại chứng từ đến với ticket của bước (08–18).
6. **Hành động đề xuất được** chỉ là bước tiến mà đầu vào duy nhất là chứng từ
   (`PREPARABLE_ACTIONS`); policy 1.0.0 từ chối bước PO (AI-14 trở đi) và bước cần lý do,
   tên NCC hay mã khi nạp.
7. **Chứng từ dựng khi duyệt** in đủ trường, giá cũng vậy, như file người tải lên; ai đọc
   được chứng từ của hồ sơ thì đọc được file. Chưa bước nào Elmich bật có trường giá; ticket
   PO (AI-14) phải quyết lại điểm này trước khi bật.

## Sửa đổi 2026-10-09 (tạm, lát AI-06; cổng mô hình)

Điểm 10 thành cơ chế: policy `supply_chain_model_routes@1.0.0` đặt profile cho từng tác vụ
(`extract.<loại chứng từ>`); tác vụ không có route chạy trên profile của process (`luna`).
Route tới profile ngoài `ungated_profiles` chỉ nạp được khi `evals/gates/<profile>.json` là
kết quả **live** của dataset cổng (`supply_chain_preparation@1.0.0`) qua tác vụ đó: không ca
an ninh nào trượt và tỉ lệ đạt ≥ `min_pass_rate` (1.0). Ngưỡng có một chủ là policy; file
kết quả chỉ có điểm. Chạy mock chỉ chứng minh script và bảng, không bao giờ là bằng chứng.
Profile `qwen` là chỗ giữ, chưa đo.
