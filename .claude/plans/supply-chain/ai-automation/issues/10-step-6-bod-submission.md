# 10 — Bước 6: tờ trình BGĐ trên approval

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/03-drafts-and-templates.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

BGĐ thấy một tờ trình AI soạn (sản phẩm, NCC, các vòng mẫu, kết luận biên bản, giá nếu người xem có scope, rủi ro, khoảng trống) ngay trên trang cấp mã duyệt.

## Việc cần làm

1. `DocumentType` mới `bod_submission` (CHECK, enum zod, test bằng nhau).
2. Prompt `draft_bod_submission@1.0.0`; mỗi câu dẫn một chứng từ hoặc dòng lịch sử; code kiểm
   dẫn chứng như bộ kiểm của brief.
3. `pass_sample` vẫn khởi động approval BGĐ hiện có; run chuẩn bị gắn tờ trình vào approval
   trước khi báo BGĐ; thiếu tờ trình không chặn duyệt (ghi "chưa có tờ trình").
4. Tin Zalo vẫn không mang mã và không giá.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không có bảng mới: tờ trình là một `document_drafts`; unit và eval có hồ sơ tenant/workspace khác qua adapter quên RLS, 0 lượt gọi.)_
- [x] Câu dẫn chứng từ của hồ sơ khác bị loại.
- [x] Người không có `commercial.read` không thấy giá trong tờ trình (bản dựng theo người xem hoặc không giá).
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; một vòng thật qua runner Postgres nợ.)_

## Nguồn

- Slide 4, 8 PoC; ADR 0016 sửa đổi S2; ADR 0014.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-10 (agent).** Đã làm (không Docker):

- `bod_submission` đã là `DocumentType` từ AI-03 (CHECK, enum zod); mẫu
  `supply_chain.bod_submission@1.0.0` dùng nguyên.
- `PrepareBodSubmission` (worker, lane đối soát duyệt BGĐ): một tờ trình mỗi vòng (bản nháp mở
  từ lúc vòng mở được dùng lại, không gọi lần hai); bằng chứng là hồ sơ, 30 dòng lịch sử,
  biên bản đánh giá đã duyệt của vòng, báo giá đã đọc **bỏ mọi giá**; một lượt gọi
  `draft_bod_submission@1.0.0` (skill `process_part_a@1.2.0`); tóm tắt, rủi ro, đề xuất chỉ
  giữ câu qua `grounded_writing` (dẫn khóa của hồ sơ này, số có trong mục được dẫn); code
  điền mã, tên, nhóm, NCC, kết quả đánh giá (kết luận của biên bản đã duyệt), đơn giá, tiền
  tệ, MOQ có trích dẫn, ngày trình. Hồ sơ không thuộc tenant và workspace của lane: không gì
  cả, 0 lượt gọi.
- `EnsureProductApproval.submissions`: hỏi tờ trình trước khi khởi động run duyệt; payload
  approval có `bod_submission` (id, sha256, khoảng trống) hay null; drafter hỏng không chặn
  duyệt. Graph duyệt BGĐ đặt khóa đó vào payload (tương thích, giữ 1.0.0).
- Bật theo policy chuẩn bị bước (`bod_submission`, nền tảng tắt); override Elmich 1.3.0 bật.
- Web: trang duyệt hiện khối "Tờ trình BGĐ (AI soạn)" khi approval là duyệt BGĐ: đọc bản nháp
  qua `GET /drafts/{id}` (giá ẩn theo scope, ô do AI viết ghi "AI viết, đã kiểm dẫn chứng"),
  không có thì "chưa có tờ trình". Tin Zalo không đổi (không mã, không giá).
- Tác vụ `draft.bod_submission`; routes policy 1.4.0, dataset
  `supply_chain_preparation@1.4.0` (+9 ca: đúng có dấu, không dấu, chèn lệnh trong lịch sử,
  xuyên tenant, xuyên workspace, câu dẫn hồ sơ khác, số bịa, thiếu biên bản và báo giá, sai
  schema). ADR 0025 "Sửa đổi 2026-10-09 (lát AI-10)".

Mutation (control xanh trước, mỗi guard bỏ đi thì đỏ): giá báo giá tới mô hình; soạn cho hồ sơ
tenant/workspace khác (sống sót lần đầu ở một dạng: kiểm chỉ workspace, eval xuyên tenant đỏ
vì lỗi khác; sửa kiểm cả tenant); tóm tắt không qua kiểm dẫn chứng; đề xuất không qua kiểm;
kết quả đánh giá đoán khi biên bản không có kết luận (sống sót với eval, thêm unit rồi đỏ);
tenant không bật vẫn có tờ trình; soạn lại trong cùng vòng; trình duyệt trước khi có tờ
trình; drafter hỏng chặn duyệt; payload duyệt bỏ tờ trình. 10/10 đỏ.

`reviewing-feature-security`: (1) không bảng mới; tờ trình là bản nháp RLS theo workspace;
drafter kiểm tenant và workspace của hồ sơ (adapter rò, 0 lượt gọi); (2) đọc tờ trình qua
`GET /drafts/{id}` (`document.read`), giá chỉ với `commercial.read`; quyền quyết của BGĐ
không đổi (scope đóng dấu của approval); (3) tầm mới của AI: viết tờ trình, không duyệt; (4)
lịch sử, tên SP, báo giá là dữ liệu trong `<input>`, ca chèn lệnh đòi ghi "BGĐ đã duyệt"; (5)
bản nháp có audit cùng giao dịch; payload approval nêu id và sha256 của đúng bản nháp; (6) một
bản nháp mỗi vòng, plan và hạn mức kiểm trước lượt gọi.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): một vòng thật qua runner
Postgres (đề xuất bước Đạt → hồ sơ chờ BGĐ → lane đối soát soạn tờ trình → approval mang
`bod_submission` → BGĐ đọc trên trang duyệt với và không có `commercial.read` → quyết trên web
và Zalo), như `test_product_bod_review.py`.

Chưa làm, ghi lại: tờ trình khi người bấm Đạt bằng tay ở API (API không có gateway của lane);
đóng bản nháp tờ trình khi BGĐ quyết (hôm nay nó mở tới khi có vòng mới); mẫu tờ trình riêng
của Elmich (QE-21).
