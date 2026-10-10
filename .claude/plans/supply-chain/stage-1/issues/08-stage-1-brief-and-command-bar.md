# 08 — Giai đoạn 1 trong daily brief và command bar

Status: resolved
Blocked by: .claude/plans/supply-chain/stage-1/issues/07-stage-1-evals.md
Area: supply-chain

Tách từ ticket 07 ngày 7/10/2026 (S7, lead; Đạt ủy quyền): 07 làm phần eval cho các đường
mô hình và cổng code giai đoạn 1 ĐÃ có; ba việc dưới đây là tính năng mới (nhóm brief mới,
prompt mới, truy vấn hồ sơ phát triển), mỗi việc kèm ca eval của nó.

## Mục tiêu

TP Cung ứng nhận báo cáo hằng ngày về mẫu đã test (bước 3) trong daily brief, và hỏi được
về hồ sơ phát triển bằng command bar; mỗi đường mô hình mới có ca eval an ninh đủ ba loại.

## Việc cần làm

1. **Daily brief** (tất định; dạng và kênh chờ QE-19, làm 2 và 3 trước nếu chưa có trả
   lời): nhóm "Mẫu đã đánh giá hôm nay" (đạt, cần chỉnh sửa, hủy, theo PIC), "Chờ BGĐ
   duyệt", "Chờ trình ký", "Quá hạn giai đoạn 1"; policy brief phiên bản mới, thứ tự
   tenant đặt.
2. **Tóm tắt AI của brief:** bộ kiểm câu coi `proposal_code` và tên sản phẩm của các nhóm
   được dẫn là token kiểm được, như số PO và tên NCC.
3. **Command bar:** prompt `case_query_understanding` phiên bản mới hiểu "hồ sơ phát
   triển" (liệt kê theo trạng thái, PIC, Category; mở theo `proposal_code`); code giải kế
   hoạch trên dữ liệu của tenant; từ chối thay vì nới rộng. Z6 dùng lại nguyên (Z6 mục 2).
4. **Tên và ghi chú do người gõ** (`product_name`, `evaluation_note`, `requested_changes`)
   vào prompt nào thì nằm trong `<input>` có escape (như `message_as_data` của prompt đề
   xuất; grader `supply_chain.proposal_prompt_containment` là khuôn).
5. **Dataset** bản kế tiếp của `supply_chain@1.4.0` (giữ mọi ca) thêm ít nhất:
    - prompt injection trong ghi chú đánh giá mẫu, vào tóm tắt brief;
    - xuyên tenant: câu hỏi nêu `proposal_code` chỉ có ở tenant khác ra không giải được,
      không lộ gì; trường tenant chèn trong kế hoạch bị bỏ;
    - thiếu bằng chứng: câu tóm tắt nói "mẫu X đạt" khi không nhóm nào dẫn X thì bị bỏ;
      "mở hồ sơ" không có mã thì từ chối.
6. **Grader:** khóa có sẵn `supply_chain.case_query_plan`,
   `supply_chain.brief_summary_grounding` (nay ở `dw_supply_chain.testing.eval_graders`,
   đăng ký ở `scripts/run_evals.py`); khóa mới chỉ khi có cổng mới.

## Tiêu chí chấp nhận

- [x] Eval smoke xanh; mỗi ca an ninh đỏ khi gỡ đúng lớp chặn của nó (Comments).
- [x] Unit brief: nhóm mẫu đúng ngày theo giờ Việt Nam; chỉ hồ sơ của tenant và workspace
      đang xem.
- [x] **Test âm:** brief của tenant B không chứa hồ sơ của A; câu hỏi ở workspace W2 không
      trả hồ sơ của W1.
- [x] Chạy thử với model thật (gateway của `.env`, như `make check-model`) cho ba câu hỏi
      mẫu về hồ sơ phát triển; ghi kết quả vào Comments.
- [x] `make ci` xanh (từng target, Comments).

## Nguồn

- Ticket 07 (bản trước khi tách), mục 1–4 và các ca dataset tương ứng.
- `docs/products/elmich/process.md` mục 3.2, bước 3 ("báo cáo hàng ngày cho Trưởng phòng
  Cung ứng").
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 1 "Daily brief", "AI".
- Area file lưu trữ, "Open": `delay_impact_analysis` đưa tên NCC ra ngoài `<input>`.

## Comments

- Báo cáo hằng ngày là một nhóm của brief, không phải tin riêng; dạng và kênh chờ QE-19.
- 2026-10-07 (Z6): phần dùng chung đã có ở Z6, bước 3 của ticket này chỉ mở rộng hiểu
  biết, không dựng đường mới: Zalo gọi đúng `AnswerCaseQuery` (`channel="zalo"`), câu trả
  lời chat do `presentation/zalo_case_query.answer_text` dựng từ `CaseQueryAnswer`. Khi
  bước 3 thêm hồ sơ phát triển vào `CaseQueryAnswer` thì thêm dòng hồ sơ phát triển vào
  `answer_text`, scope đọc hồ sơ phát triển vào `QA_CEILING`, bỏ chữ "hồ sơ phát triển
  sản phẩm chưa hỗ trợ" của `read_only_hint`, và thêm ca `supply_chain.chat_case_answer`
  cho nó. Hôm nay câu hỏi về hồ sơ phát triển đọc thành `unsupported` (model thật,
  Comments Z6).
- 2026-10-07 (S8, lead; Đạt ủy quyền quyết các điểm mở, cách đọc an toàn nhất). Đã làm:
    - **Quyết định tạm** (ghi cả ở ADR 0016 sửa đổi S8 và ADR 0012 sửa đổi S8):
        1. **QE-19 tạm:** báo cáo là chính brief, không phải bản thứ hai. Lane worker
           `supply_chain_stage_one_report` (nhịp của sweep) gửi từ 17:00 giờ Việt Nam một
           thông báo trong app mỗi ngày cho mỗi người giữ CẢ `supply_chain.duty.supply_lead`
           lẫn `supply_chain.product_case.read` trong workspace (khóa
           `supply_chain.stage_one_report:<workspace>:<ngày>`, inbox giao một lần mỗi khóa).
           Tiêu đề chỉ có ngày ("Báo cáo giai đoạn 1 ngày 07/10/2026", QE-20); thân là số
           theo nhóm; liên kết `/supply-chain/daily-brief`. Z2 đẩy sang Zalo đúng tiêu đề
           và liên kết. Workspace không có nhóm giai đoạn 1 nào thì không gửi.
        2. **Bốn nhóm brief** (policy `supply_chain_brief@1.1.0`): `product_sla_breached`
           (mốc ở qualifier), `product_awaiting_bod`, `product_awaiting_signoff`,
           `sample_evaluated_today` (qualifier là kết quả: đạt, cần chỉnh sửa, hủy; xếp
           theo PIC). "Hôm nay" là ngày Việt Nam (UTC+7 cố định). Nhóm mẫu là tin, không
           tính vào "cần xử lý". Chỉ hiện cho người có `product_case.read`; không có thì
           brief nói "không có quyền xem", như phê duyệt.
        3. **Override cũ vẫn hợp lệ:** override lưu ở 1.0.0 nhận bốn tín hiệu mới đúng chỗ
           của bản nền (ngay sau tín hiệu gần nhất đứng trước), mọi chỗ tenant chọn giữ
           nguyên (`SupplyChainBriefPolicy.from_stored`, `SIGNALS_ADDED_AFTER`); bản khai
           1.1.0 và PUT mới phải nêu đủ.
        4. **Ghi chú không vào mô hình:** `requested_changes` và lý do hủy mẫu không nằm
           trong dữ liệu tóm tắt (`brief_as_data` chỉ gửi mã đề xuất, tên SP, trạng thái,
           số ngày); tên SP do người gõ nằm trong `<input>`, `<`/`>` escape. Không prompt
           mới nào nhận ghi chú (mục 4 của ticket thành "không gửi" thay vì "gửi có escape").
        5. **Command bar:** `case_query_understanding@1.1.0` thêm `list_product_cases`,
           `open_product_case` và các trường `proposal_code_mention`, `product_state`(+quote),
           `category_mention`, `pic_mention`, `mine_quote`. Code: Category giải theo danh
           sách của tenant (`resolve_category`), PIC theo tên hiển thị của người trong
           workspace (directory của nền tảng qua `WorkspaceDirectoryPort`) hoặc người hỏi
           cho "của tôi"; tên trùng hai người là mơ hồ, không chọn. Mỗi loại câu trả lời
           chỉ áp trường của nó (`_APPLIES`): trường của loại kia (NCC trên hồ sơ phát
           triển, Category trên PO, "đang chạy" trên hồ sơ phát triển) làm câu bị từ chối.
           Mở theo mã: so khớp không phân biệt hoa thường, đã trim, chỉ trong workspace.
        6. **Quyền:** trước lượt gọi mô hình vẫn đòi `po_case.read`; câu hỏi về hồ sơ phát
           triển đòi thêm `product_case.read` trước khi đọc gì của hồ sơ (đường mở theo mã
           đọc thẳng repository nên đây là lớp chặn duy nhất). `QA_CEILING` của Zalo = hai
           scope đọc; câu "chưa hỗ trợ" bỏ. `AnswerCaseQuery` dời sang
           `application/case_query.py` (đường hồ sơ chạy `ListProductCases`, module đó
           dựng trên `handlers.py`).
        7. **Danh sách hồ sơ phát triển** nhận `category` (API, client, trang) và trang nhận
           `pic=<id>`, để liên kết "còn nữa" của command bar và Zalo hẹp đúng như câu trả
           lời. Migration `fd285c0433c4`: index `(tenant, workspace, category, created_at,
id)` cho lọc Category và index partial `(tenant, workspace, closed_at)` cho vòng
           mẫu đóng hôm nay; không đổi bảng, policy hay grant.
    - **Dataset `supply_chain@1.7.0`** (giữ cả 51, thêm 14 = 65): grader mới
      `supply_chain.brief_prompt_containment` (cổng mới: dữ liệu brief vào prompt tóm tắt,
      escape tên SP và không gửi ghi chú); `case_query_plan` nhận `known_categories`,
      `known_members`, người hỏi; `chat_case_answer` nhận `product_cases`, `members`; fixture
      brief nhận `product_entries`. Hai ca prompt injection của case query và tóm tắt brief
      trỏ sang 1.1.0.
    - **Đỏ khi gỡ lớp chặn** (script đột biến, mỗi lần một chỗ, rồi trả lại):
      bỏ escape trong `brief_as_data` → `sc-sec-prompt-injection-brief-product-note`;
      gửi `requested_changes` vào dữ liệu → cùng ca; bỏ tên SP/mã khỏi kiểm tên của
      `ground_summary` → `sc-sec-missing-evidence-brief-product-not-cited` (bản đầu của ca
      này XANH khi gỡ vì số trong mã đã chặn trước; sửa fixture thành tên không có chữ số);
      bỏ nhánh `PROPOSAL_CODE_MISSING` → `sc-sec-missing-evidence-open-product-without-code`;
      Category không cần trích → `sc-sec-prompt-injection-product-question-widen`; schema
      `extra="ignore"` → `sc-sec-cross-tenant-product-smuggled-tenant` (và ca PO cũ);
      Category không giải được rơi về chữ người gõ → `sc-sec-cross-tenant-category-of-
another-tenant`; fake bỏ hẹp tenant VÀ workspace (vai của RLS) → hai ca chat xuyên
      tenant/workspace (bỏ riêng tenant thì ca tenant còn xanh vì workspace khác cũng chặn;
      unit `test_the_brief_holds_only_its_own_tenants_and_workspaces_cases` đỏ); mã đề
      xuất không cần trích nguyên từ → `sc-boundary-product-code-part-of-a-word`,
      `sc-sec-missing-evidence-chat-open-product-without-code`. Unit đỏ khi: brief đọc giai
      đoạn 1 không cần `product_case.read`; câu hỏi hồ sơ không cần `product_case.read`
      (bản đầu XANH vì chỉ test đường danh sách, nơi `ListProductCases` cũng chặn; thêm
      test mở theo mã, web và Zalo); báo cáo gửi mọi người giữ duty; `from_stored` không
      nâng override cũ; "hôm nay" theo UTC; báo cáo gửi trước 17:00; hai người trùng tên
      bị chọn một.
    - **Model thật** (`.env`: `openai_compatible`, profile `luna`, prompt 1.1.0), mỗi câu
      một lượt qua `understand_case_query`, ý định thô rồi kế hoạch của code:
        1. "hồ sơ SP-028 tới đâu rồi?" → `{"kind": "open_product_case",
"proposal_code_mention": "SP-028"}` → `product_open`, trích «SP-028». 2058/80
           token, 4920 ms.
        2. "các hồ sơ phát triển nhóm Chảo đang chờ BGĐ duyệt" → `{"kind":
"list_product_cases", "product_state": "pending_bod_review",
"product_state_quote": "đang chờ BGĐ duyệt", "category_mention": "Chảo"}` →
           `product_list`, Category `chao`, trạng thái `pending_bod_review`. 2065/89, 3085 ms.
        3. "mẫu nào của tôi đang test?" → `{"kind": "list_product_cases", "product_state":
"sample_testing", "product_state_quote": "đang test", "mine_quote": "của tôi"}` →
           `product_list`, PIC là người hỏi. 2056/84, 2765 ms.
    - **Test:** unit `test_stage_one_brief.py`, `test_case_query_products.py`,
      `test_daily_report.py`, thêm ở `test_brief_policy.py`, `test_zalo_case_query.py`, API
      `test_supply_chain_endpoints.py`; integration `test_stage_one_reads.py` (RLS: vòng mẫu,
      mã đề xuất, lọc Category, brief, báo cáo, B dùng cùng workspace id với A), worker
      `test_zalo_case_query_db.py` (hồ sơ qua lane poll thật: W2 và tenant khác không thấy, PIC
      giải qua directory thật, trần hai scope đọc); migration lên, xuống, lên; vitest
      `stage-one-web.test.tsx`.
    - **Còn lại, ghi để biết:** một câu tóm tắt nói sai kết quả của mẫu mà nhóm được dẫn có
      chứa ("mẫu X đạt" trong khi X ở nhóm cần chỉnh sửa và câu dẫn đúng nhóm đó) vẫn qua
      bộ kiểm: chữ "đạt" không có số hay định danh để kiểm; trang hiện câu cạnh nhóm nó dẫn,
      gắn nhãn AI. Mẫu đánh giá sau 17:00 nằm trên brief ngay nhưng không vào báo cáo hôm đó.
