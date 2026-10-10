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

## Sửa đổi 2026-10-09 (tạm, lát AI-07, AI-08; mô hình viết, code giữ)

1. **Bản nháp do mô hình viết** đi qua một chỗ: `domain.grounded_writing`. Mô hình viết
   câu, mỗi câu dẫn khóa bằng chứng code đã gom (chỉ của hồ sơ này, chỉ thứ người soạn được
   đọc); code giữ câu mà mọi khóa có trong bằng chứng, mọi con số có trong mục được dẫn,
   không có gì giống số tài khoản, và loại câu khác (đếm, không sửa). Tác vụ viết có tên
   `draft.<loại>` trong `supply_chain_model_routes` và có ca trong dataset cổng như tác vụ
   đọc.
2. **Bước 1 từ một danh sách** (AI-08) không phải đề xuất bước: chưa có hồ sơ để trình.
   File danh sách lưu trong PostgreSQL (`proposal_lists`, ≤ 10 MiB, chỉ thêm), không trong
   bucket, như override mẫu của tenant: nó chưa thuộc hồ sơ nào nên không có khóa đối tượng
   theo hồ sơ, và sweep mồ côi không phải học tiền tố mới. Lane `supply_chain_proposal_lists`
   đọc thành từng dòng; mỗi dòng thành hồ sơ chỉ khi PIC bấm, qua đúng `ProposeProductCase`
   (PIC đóng dấu từ người bấm, nhóm phải là khóa của tenant, mã trùng là lỗi của cơ sở dữ
   liệu). Việc đề xuất rồi ghi quyết định của dòng là hai giao dịch: dòng đã có quyết định
   thì bị từ chối trước khi tạo hồ sơ; hai lần bấm cùng lúc với mã khác nhau vẫn có thể tạo
   hai hồ sơ (cửa sổ nhỏ, ghi lại; khóa idempotency của route chặn bấm đúp cùng giá trị).

## Sửa đổi 2026-10-09 (tạm, lát AI-09; bước có số đo và nhiều kết quả)

1. **Một bước vật lý có thể có nhiều kết quả.** `PreparedStep.outcomes` (lựa chọn → hành
   động, nhãn) và `outcome_field` (ô kết quả chọn nó). Approval vẫn là một, kiểu theo
   hành động chính (`pass_sample`); người duyệt chọn trong ô kết quả, và hành động được áp
   là hành động của lựa chọn. Hành động cần lý do (`request_revision`, `reject_sample`,
   `OUTCOME_REASON_ACTIONS`) lấy nhận xét bắt buộc của người duyệt làm lý do. Lựa chọn
   ngoài danh sách bị 422 trước khi quyết. Policy kiểm khi nạp: ô chọn là ô kết quả của
   bước vật lý, mọi hành động đi từ trạng thái đó, giấy của hành động nào cần giấy thì được
   soạn hay là nguồn. Các hành động thuộc duty khác nhau thì lane không trình (một approval
   chỉ đóng dấu một scope; `outcome_duties_differ`).
2. **Giấy của kết quả không chọn bị đóng**, không xác nhận: khi Đạt, phiếu chỉnh sửa nháp
   nhận quyết định `rejected` ("không dùng") trong cùng giao dịch; biên bản (giấy mang kết
   quả) luôn được xác nhận.
3. **Số đo của vòng là chủ thể.** `sample_measurements` (chỉ thêm, mới nhất của mỗi tiêu chí
   tính); tiêu chí và ngưỡng ở policy `supply_chain_sample_criteria` (tenant ghi đè, nền
   tảng chỉ có tiêu chí kiểm trung tính); phép so là của code (`SampleCriterion.verdict`),
   một chỗ cho bảng biên bản, mục phiếu, gợi ý kết luận và trang nhập số đo. Phiên bản chủ
   thể của đề xuất gồm số đo của vòng: nhập số sau khi trình thì đề xuất cũ và được chuẩn
   bị lại.
4. **Nguồn của vòng là của vòng.** Loại chứng từ bước vừa đọc vừa soạn (biên bản) chỉ tính
   khi tải lên từ lúc vòng mở: biên bản vòng trước đã duyệt không phải báo cáo test vòng
   này. Chuẩn bị và chủ thể đọc nguồn qua cùng một hàm (`step_documents`).
5. **Mô hình viết, không kết luận.** Một lượt gọi (`draft_sample_evaluation@1.0.0`) viết ghi
   chú cho biên bản và một yêu cầu cho mỗi tiêu chí trượt; code giữ ghi chú qua
   `grounded_writing`, giữ yêu cầu chỉ dưới tiêu chí trượt mà nó dẫn. Không có lời của mô
   hình (không plan, hết lượt, sai schema) thì bản nháp vẫn có bảng của code và các ô trống
   có tên. Trường do mô hình viết mang nguồn `ai_written` với các khóa nó dẫn; trang hiện "AI
   viết, đã kiểm dẫn chứng", không bao giờ như giá trị máy đọc hay người kiểm.

## Sửa đổi 2026-10-09 (tạm, lát AI-10; tờ trình BGĐ)

1. **Tờ trình gắn vào approval có sẵn, không phải đề xuất bước.** Bước 6 không có hành
   động một người bấm (`bod_approve`, `bod_reject` là của graph); `pass_sample` vẫn làm
   hồ sơ chờ BGĐ và lane đối soát vẫn trình approval `supply_chain.product_action.bod_review`.
   Trước khi khởi động run duyệt (nên trước khi báo BGĐ), `EnsureProductApproval` hỏi
   `PrepareBodSubmission`; payload của approval mang `bod_submission` (id bản nháp, sha256,
   khoảng trống) hay null. Thêm một khóa payload là thay đổi tương thích: graph giữ phiên bản
   1.0.0 để run đang chờ duyệt vẫn resume được.
2. **Tờ trình là bản nháp, không thành chứng từ.** Giá là trường của bản nháp, nên ai đọc
   (trang duyệt, xem trước) thấy theo scope của mình; không có file nào in giá cho người đọc
   chứng từ (điểm 7 của sửa đổi AI-05 không bị mở rộng).
3. **Mô hình không thấy giá.** Bằng chứng gồm hồ sơ, lịch sử, biên bản đã duyệt và báo giá
   đã đọc bỏ mọi trường giá; đơn giá, tiền tệ, MOQ do code điền từ bản đọc có trích dẫn.
4. **Bật theo tenant** (`bod_submission` của policy chuẩn bị bước; nền tảng: tắt). Chỉ host
   có gateway (worker) soạn; API trình duyệt khi người bấm bước bằng tay thì không có tờ
   trình. Mô hình hỏng, hết lượt, sai schema: không tờ trình, duyệt vẫn trình.

## Sửa đổi 2026-10-09 (tạm, lát AI-13; bước 9 do code chuẩn bị)

1. **Không mô hình ở bước 9.** `DraftRecipe.ITEM_CODING` điền phiếu mã hàng (`official_item_code`,
   loại chứng từ đã có, "Mã hàng chính thức" của `process.md`; ticket ghi loại mới, không cần:
   một sự việc, một loại) từ hồ sơ, quy tắc mã của tenant và biến thể của phiên bản BM04 mới
   nhất; mẫu `supply_chain.official_item_code@1.0.0`. Eval của bước khẳng định 0 lượt gọi.
2. **Đề xuất bước 9 mang nhiều bước.** Hành động đích vẫn là một (`submit_for_signoff`, duty
   `ordering`); duyệt thì phiếu đã xác nhận cấp mã (nếu hồ sơ chưa có hay khác), thêm SKU còn
   thiếu, rồi trình ký, mỗi bước qua đúng bảng dispatch một cú bấm dùng, audit như người quyết,
   cùng giao dịch với chứng từ. Phiếu không có mã hàng hay không có SKU nào bị từ chối trước khi
   dựng hay lưu gì. Lane đối soát vẫn trình phần ký như trước.
3. **Chủ thể** của đề xuất bước 9 thêm phiên bản BM04 mà SKU lấy từ đó và câu trả lời "mã nào
   đã bị chiếm" cho các mã trong payload (`coding`: chỉ mã, không giá).

## Sửa đổi 2026-10-10 (tạm, lát AI-14; bước 10 duyệt bằng chính `create_po`)

1. **PO nháp không qua approval của nền tảng.** `create_po` là lệnh có ô người nhập (số PO), là
   bước của Hồ sơ PO (không phải hồ sơ phát triển mà graph chuẩn bị bước, `step_preparations`
   và payload `product_dev_case_id` phục vụ), và Zalo không mang được số PO. Nên bước 10 là: lane
   `supply_chain_purchase_orders` soạn MỘT bản nháp `purchase_order` cho mỗi Hồ sơ PO chờ tạo PO
   (bật bằng `purchase_order: true` của policy chuẩn bị bước; nền tảng tắt), báo Cung ứng (duty
   của `create_po`) và Kế toán (`finance`) một lần, không kèm giá; Cung ứng nhập số PO và bấm
   "Duyệt PO" trên trang Hồ sơ PO. Duyệt cần đúng scope duty của `create_po` (đọc từ policy duty
   PO, như tạo tay) VÀ `supply_chain.commercial.write` (duyệt ghi giá); bản nháp phải là phiên
   bản mở mới nhất người đó thấy (`content_sha256`). Nút tạo PO tay vẫn còn (điểm 7).
2. **Không mô hình nào được hỏi.** Mọi ô do code điền: NCC, dòng hàng, số lượng từ Hồ sơ PO; điều
   khoản và đơn giá người đã nhập trên hồ sơ trước; rồi BM04 mới nhất (đơn giá, tiền tệ,
   Incoterm) trừ khi thư chốt NCC đọc ở bước 8 ghi khác (mâu thuẫn, ô trống, phát hiện); ô chỉ thư
   nêu thì lấy từ thư. Điều khoản thanh toán, % cọc, ngày giao không bao giờ đoán.
3. **Tổng của code.** Thành tiền, tổng, tiền cọc tính bởi `domain.purchase_order_draft`; tổng nào
   trong bản nháp (người sửa hay bất kỳ ai ghi) khác số code tính là phát hiện trên trang và bị từ
   chối khi duyệt. Dòng thiếu số lượng hay đơn giá, hoặc thiếu tiền tệ: không duyệt được; điều
   khoản khác thiếu thì vẫn duyệt được (Kế toán thấy chỗ trống).
4. **Một giao dịch:** phiên bản bản nháp mang số PO và quyết định xác nhận, chứng từ
   `purchase_order` dựng từ đó (`origin = ai_prepared`), bước `create_po` (số PO, trạng thái, số
   lượng, lịch sử), điều khoản và đơn giá của Hồ sơ PO, mọi audit; số PO đã có trong công ty hay
   bản nháp đã quyết thì không gì được ghi. Kế toán được báo sau khi ghi, không kèm giá.

## Sửa đổi 2026-10-10 (tạm, lát AI-15; bước Hồ sơ PO do code chuẩn bị, duyệt trên trang Hồ sơ PO)

1. **Bước 11-17 theo mẫu AI-14, không qua approval run.** Mỗi bước Hồ sơ PO AI chuẩn bị là một
   `POStepKind` (`domain.po_step`): trạng thái đi từ, hành động đích, giấy code soạn, chứng từ đọc,
   ô kết quả người nhập, có ghi giá hay không. Tenant bật theo tên (`po_steps` của policy chuẩn bị
   bước; nền tảng: không bước nào; Elmich 1.8.0: bốn bước của AI-15). Việc bước làm là code, không
   phải policy.
2. **Lane `supply_chain_po_steps`** soạn MỘT giấy cho mỗi hồ sơ ở trạng thái của bước có giấy (bị
   từ chối thì không soạn lại), báo người giữ duty của hành động đích theo policy duty của tenant,
   không kèm số tiền. Không mô hình: mô hình chỉ đọc chứng từ ở lane trích xuất.
3. **Trang Hồ sơ PO** (`GET /po-cases/{id}/step-proposal`) tính phát hiện lúc mở (chứng từ thiếu,
   chưa đọc, không đọc được; số tiền, tiền tệ, dòng, tài khoản không khớp; số trong bản nháp khác
   code) và gợi ý cạnh mỗi ô kết quả để trống (số tiền ẩn khi thiếu quyền xem giá). "AI đề xuất"
   khi không còn phát hiện; phát hiện không bao giờ tự chặn, người quyết.
4. **Duyệt** (`POST .../step-proposal/approval`): scope duty của hành động đích theo policy của
   tenant, thêm `commercial.write` khi bước ghi giá; bước phải bật; hồ sơ đúng trạng thái; bản nháp
   là phiên bản mở người đó thấy, mọi số là số code tính; ô kết quả hợp lệ; giấy tenant bắt buộc có
   trên hồ sơ. Một giao dịch: xác nhận bản nháp, chứng từ `ai_prepared`, bước qua đúng
   `apply_action`, khoản thanh toán (`po_payments`), audit (không số tiền).
5. **Giấy bắt buộc của bước** (QE-02, tạm): policy `supply_chain_po_documents@1.0.0` (nền tảng:
   không; Elmich: `confirm_deposit` cần `deposit_docs`, `confirm_payment` cần `payment_docs`), hỏi ở
   ba cửa của một bước: người bấm (`AdvancePOCase`), apply node của graph duyệt theo ma trận, và
   đề xuất bước.

## Sửa đổi 2026-10-10 (tạm, lát AI-17; bước 13-15, kết quả là lựa chọn của người)

1. **Ba bước nữa theo mẫu AI-15:** `production` (bước 13, `send_to_qc`, đọc lịch sản xuất, ô ETD
   không bắt buộc), `qc` (bước 14) và `arrival` (bước 15, `arrive_at_port`, đọc giấy báo hàng đến,
   B/L, packing list, hóa đơn thương mại, C/O; ô ETA bắt buộc). Elmich 1.10.0 bật cả ba.
2. **Một bước, hai kết quả:** bước `qc` có ô lựa chọn `qc_result` (Đạt / Không đạt) do QC nhập;
   hành động đích suy từ lựa chọn (`pass_qc`, `fail_qc`) TRƯỚC khi kiểm duty, nên duty kiểm là duty
   của hành động thật. Ô `reason` chỉ bắt buộc khi chọn Không đạt (`required_for`), ô số container
   (ISO 6346, code chuẩn hóa) không bắt buộc. Gợi ý của code cạnh ô theo SỐ lỗi so với Ac báo cáo
   ghi, không theo chữ "PASS"; chữ khác số là phát hiện đỏ. Không có bảng AQL trong code: Ac lấy từ
   báo cáo, thiếu thì "không đọc được số lỗi".
3. **Giấy chỉ của một kết quả:** phiếu yêu cầu sửa hàng (`rework_request`) được soạn khi số lỗi vượt
   Ac (`draft_optional`); duyệt Không đạt thì phiếu thành chứng từ cùng bước, duyệt Đạt thì bản nháp
   bị đóng (`rejected`, lý do ghi hành động đã đi) trong cùng giao dịch.
4. **Ngày và container ghi cùng bước:** `po_cases.etd`, `eta`, `container_number` (migration
   `0c3b3a73be30`, CHECK ISO 6346) chỉ do bước đã duyệt ghi, từ ô người nhập, không từ gợi ý.
5. **Phép kiểm của code:** lịch sản xuất có ETD muộn hơn ngày giao dự kiến của PO; packing list so
   từng SKU với PO (không giá); số container của packing list phải có trên B/L và giấy báo; hồ sơ hải
   quan theo skill `customs_file@1.1.0` (C/O là tùy chọn); bước 16 thêm packing list và báo cáo QC
   mới nhất (đối chiếu ba bên). Phát hiện không bao giờ tự chặn.
6. **Đóng cont vẫn không phải bước riêng** (QO-6, chờ QE-15): số container nằm trên `pass_qc`.
   Biên bản test trước SX chưa do AI soạn (bước 13 hôm nay chỉ đọc lịch). Ảnh và bản quét không đọc
   được (chưa có OCR): bước nêu "máy không đọc được", người kiểm.
7. **Thư hỏi tiến độ hằng tuần** (`production_progress`, mẫu 1.2.0, prompt
   `draft_supplier_message@1.2.0`): một thư mỗi tuần ISO cho mỗi Hồ sơ PO đang sản xuất, dẫn mốc và
   ETD đúng như lịch NCC đã đọc; người gửi (E18). Mẫu của tenant lưu ở phiên bản cũ lấy mẫu nền tảng
   cho mục đích mới (`PURPOSES_ADDED_AFTER`).

## Sửa đổi 2026-10-10 (tạm, lát AI-18; bước 17, số đếm là của Kho)

1. **Bước `warehouse`** (`warehouse_receiving` → `complete`, duty `warehouse`; Elmich 1.11.0): lane
   soạn phiếu nhập kho từ dòng PO (SKU, số đặt) và số packing list ghi đã giao; cột số đếm để trống.
2. **Số đếm theo dòng, không phải ô kết quả:** bước có `counts`; trang trả từng dòng PO với số giao
   bên cạnh ô đếm trống; duyệt gửi `counts` (SKU → số nguyên 0..10.000.000). Thiếu số của một dòng,
   số cho dòng không có trong PO, số ngoài khoảng: từ chối, không ghi gì. Số giao là gợi ý, không
   bao giờ điền vào ô.
3. **Một giao dịch:** bước, phiếu nhập kho thành chứng từ (render với số đếm vừa nhập ở cột đếm),
   mỗi dòng một hàng `po_case_line_receipts` (chỉ thêm; số đặt, số giao, mã SKU đóng dấu cạnh số đếm,
   trích phiếu), audit (số dòng đếm, số dòng chênh lệch).
4. **Một câu trả lời cho "dòng nào chênh":** `receipt_check.discrepancies` (đếm khác số giao, packing
   list không ghi thì khác số đặt) là nguồn của audit, biên bản chênh lệch và thư khiếu nại. Lane
   PO step soạn MỘT `discrepancy_report` cho mỗi hồ sơ có dòng chênh trong 30 ngày gần nhất và báo
   Cung ứng; lane thư soạn MỘT thư `discrepancy_claim` dẫn đúng các dòng đó (không giá); người gửi.
   Biên bản là bản nháp xem trước, sửa, tải về, như yêu cầu sửa của AI-16.

## Sửa đổi 2026-10-10 (tạm, lát AI-17 mục 2; biên bản test trước SX như vòng mẫu)

1. **Số đo của R&D** (`pre_production_measurements`, migration `2bb10bdd4420`: chỉ thêm, workspace
   RLS, `dw_app` SELECT/INSERT): cùng danh sách tiêu chí của vòng mẫu (`supply_chain_sample_criteria`
   theo nhóm của hồ sơ sản phẩm; không có hồ sơ sản phẩm thì danh sách mặc định), theo **lần test**
   (lần đầu, cộng một mỗi lần Không đạt trong lịch sử bước 12). Nhập cần duty của bước Đạt test (R&D),
   chỉ khi đã nhận mẫu và test chưa đạt. Phép so là `sample_evaluation.judge`, một chỗ.
2. **Biên bản** (`pre_production_test_report@1.0.0`): lane bước 12 soạn khi mọi tiêu chí của lần test
   đã có số: bảng của code, ghi chú do mô hình viết bằng CHÍNH prompt và grounding của biên bản vòng
   mẫu (`draft_sample_evaluation@1.0.0`, tác vụ `draft.sample_evaluation`), chỉ giữ câu dẫn đúng bằng
   chứng; mô hình không trả lời thì biên bản chỉ có bảng. Ngày, người test và kết luận để trống. Một
   bản cho mỗi bộ số; số sửa sau thì soạn bản mới. R&D được báo.
3. **Gợi ý cạnh ô trống:** Không đạt khi một tiêu chí trượt, Đạt khi mọi tiêu chí đạt, không gợi ý khi
   còn tiêu chí chưa đo; không chọn sẵn.
4. **Biên bản thành chứng từ ở bước Đạt / Không đạt:** bước test nhận `draft_id` thay cho file tải
   lên. Bản nháp phải là bản mở của hồ sơ này, đúng loại, và bảng tiêu chí phải đúng bằng kết quả của
   số đo hiện tại (số đổi sau khi soạn, hay bảng bị sửa tay: từ chối). Bước được kiểm mở trước; bản
   nháp render với kết luận R&D chọn, lưu, ghi chứng từ và xác nhận bản nháp trong một giao dịch
   (`DraftFiler`, `SqlDraftFilings`), rồi bước ghi với chứng từ đó. Bước thất bại sau đó để lại chứng
   từ đã lưu, như một file tải lên.

## Sửa đổi 2026-10-10 (lát AI-19; trợ lý hồ sơ chỉ đọc)

1. **Một lượt gọi có căn cứ, không phải agent dùng tool.** Ticket 19 viết tool spec, toolset và
   `build_agent`; lát này chọn cách hẹp hơn: code gom bằng chứng của MỘT hồ sơ (lịch sử 30 dòng mới
   nhất, BM04, số đo vòng mẫu, trường đã đọc của 20 chứng từ mới nhất), một lượt gọi
   (`answer_case_question@1.0.0`, tác vụ `draft.case_answer`), code giữ câu qua `ground_sentences`.
   Lý do: câu hỏi chỉ đọc về một hồ sơ không cần vòng lặp; mọi thứ mô hình thấy là thứ code đã chọn
   theo quyền người hỏi, nên không có tool nào để agent gọi sai, không có `prepare_step` để từ chối.
   Khi cần hỏi xuyên nhiều hồ sơ, agent qua tool là lát sau.
2. **Không rộng hơn người hỏi:** đọc của loại hồ sơ trước khi đọc gì; nội dung chứng từ chỉ với
   `document.read`; giá (`PRICE_FIELDS`, chứng từ in số tiền) chỉ trên cổng với `commercial.read`,
   không bao giờ qua Zalo (QE-20 tạm: nội dung không giá được qua Zalo, Đạt 2026-10-10). Zalo chạy
   dưới trần hai quyền đọc của kênh, nên không thấy chứng từ.
3. **Không trích được thì nói "không đủ bằng chứng"**, kể cả khi mô hình trả sai schema.
4. Cổng: `POST /po-cases/{id}/questions`, `POST /product-cases/{id}/questions` (thẻ "Hỏi về hồ sơ");
   Zalo: câu hỏi mở đúng một hồ sơ thì trả lời thêm nội dung, mỗi câu kèm nguồn.

## Sửa đổi 2026-10-10 (lát AI-20; báo cáo và đo AI được duyệt)

1. **Số do code, chữ do AI kiểm với số:** báo cáo tuần (thứ Hai 00:00 tới thứ Hai sau, giờ Việt Nam)
   đếm từ lịch sử hai loại hồ sơ, mỗi số nêu các hồ sơ được đếm; câu tóm tắt
   (`summarize_weekly_report@1.0.0`, tác vụ `draft.weekly_report`) chỉ giữ khi mọi số trong câu là số
   được dẫn. Điểm NCC đếm PO, QC trả làm lại, vòng mẫu, dòng kho đếm lệch; xếp theo số PO, không xếp
   hạng theo điểm.
2. **AI được duyệt bao nhiêu:** mỗi dòng dõi bản nháp (phiên bản 1 là của lane) tính một lần: duyệt
   nguyên, sửa rồi duyệt, từ chối, còn mở. Phút tiết kiệm là ước tính từ chính sách
   `supply_chain_ai_time_saved@1.0.0` (tenant ghi đè được; số tạm, chờ Elmich đo), trang ghi rõ là ước
   tính. Đây là đầu vào cho QA-6 (nới bước nào không cần người), không tự nới gì.
3. Không bảng mới, không ghi gì; đọc cần cả hai quyền đọc hồ sơ. Tỷ lệ việc qua Zalo (slide 13) chưa
   đo ở lát này: quyết định trên Zalo chưa ghi kênh vào bảng có thể đếm.
