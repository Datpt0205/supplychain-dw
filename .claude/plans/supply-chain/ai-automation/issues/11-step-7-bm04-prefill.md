# 11 — Bước 7: BM04 điền sẵn, nêu khoảng trống

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/01-commercial-data-and-bm04-fields.md, .claude/plans/supply-chain/ai-automation/issues/02-document-extraction-lane.md, .claude/plans/supply-chain/ai-automation/issues/05-step-proposals.md
Area: supply-chain

## Mục tiêu

Khi hồ sơ vào `profile_in_progress`, AI điền bản nháp BM04 có trường từ hồ sơ, biên bản đạt và báo giá/spec của NCC; ô thiếu hoặc mâu thuẫn được nêu, không đoán (slide 8).

## Việc cần làm

1. Prompt `draft_bm04@1.0.0` + skill `bm04_guide`; nguồn: hồ sơ, trích xuất biên bản,
   `supplier_quotation`.
2. Code: hai nguồn khác giá trị → mâu thuẫn; trường bắt buộc của schema tenant không có
   nguồn → khoảng trống.
3. Duyệt `complete_profile`: phiên bản `product_profiles` + file BM04 dựng từ mẫu thành
   chứng từ `product_profile_bm04`.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới: BM04 là `document_drafts`, phiên bản hồ sơ là `product_profiles` của AI-01; unit và eval: hồ sơ tenant khác, bản đọc ở workspace khác, 0 lượt gọi, không bản nháp.)_
- [x] Hồ sơ không có báo giá: giá là khoảng trống, không số.
- [x] Hai nguồn giá khác nhau: mâu thuẫn nêu cả hai trích dẫn. _(Ô khác giá: nêu cả hai trích dẫn. Ô giá: nêu tên hai chứng từ, không số, vì payload của approval không lọc theo scope (E15); số giá nào code biết bị che trong mọi trích dẫn khác.)_
- [x] **Eval** (`supply_chain_preparation`, ticket 06): chèn lệnh trong file/nội dung nguồn không đổi kết quả; chứng từ của tenant B và của workspace khác cùng tenant bị từ chối trước lượt gọi mô hình (mock gateway đếm 0 lượt); ô không có trích dẫn tìm thấy là khoảng trống; số khác số code tính bị từ chối. Mỗi ca an ninh đỏ khi bỏ guard của nó (ghi đột biến vào Comments).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- Slide 8 PoC; ADR 0026 (E15); QE-05.
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-11 (agent).** Đã làm (không Docker):

- `domain/bm04_prefill.py`: `reconcile` (mỗi ô: một giá trị thì giữ kèm nguồn; hai nguồn khác
  giá trị là `Bm04Conflict`, ô trống, không chọn bên nào; số so theo giá trị, chữ chuẩn hóa);
  `ground_bm04_writing` (ô mô hình chỉ giữ khi dẫn đúng MỘT mục bằng chứng, trích dẫn nằm trong
  mục đó, giá trị nằm trong trích dẫn, số có trong trích dẫn; không bao giờ ô thương mại:
  giá, tiền tệ, MOQ, thời gian, Incoterm); `conflict_message` (giá chỉ nêu tên chứng từ; số giá
  bị che trong trích dẫn khác).
- `application/bm04_prefill.py`: `Bm04Preparation` (ứng viên của code: hồ sơ, bản đọc
  `supplier_quotation` và `product_profile_bm04` NCC gửi; đơn giá là dòng duy nhất hoặc dòng
  duy nhất ghi tên SP, nhiều dòng mà không dòng nào thì không giá và phát hiện), một lượt gọi
  `draft_bm04@1.0.0` (skill `bm04_guide@1.1.0`) chỉ cho ô còn trống, không thương mại, bằng
  chứng không giá (hồ sơ, trường đã đọc, mô tả dòng hàng, biên bản đánh giá đã duyệt);
  `Bm04ProfileWriter`: duyệt BM04 thành phiên bản `product_profiles` trong CÙNG giao dịch với
  bước (`apply_approved(profile=…)`, `insert_product_profile` dùng chung với thẻ BM04):
  thuộc tính theo schema BM04 của tenant, MOQ, thời gian, Incoterm; giá chỉ khi người duyệt có
  `commercial.write`, không thì giữ giá phiên bản trước; BM04 không có giá giữ giá trước; ô bắt
  buộc còn thiếu thì không ghi phiên bản nào (audit `product_profile.not_saved`).
- `DraftRecipe.BM04`, `StepCheck.BM04_SOURCES` (mâu thuẫn, dòng giá mơ hồ, ô bắt buộc của schema
  tenant không có nguồn); `FieldInput.source()` nay mang cả tài liệu, trích dẫn và
  `ai_written` khi mô hình đọc từ chứng từ. Override Elmich 1.4.0 bật bước 7 (`complete_profile`,
  nguồn báo giá và BM04 NCC gửi). Nhãn web cho `bm04_facts_unavailable`.
- Tác vụ `draft.bm04`; routes policy và dataset 1.6.0 (+11 ca: đúng có dấu, không dấu, chèn
  lệnh trong mô tả báo giá, xuyên tenant, xuyên workspace, thiếu bằng chứng, số bịa, mâu thuẫn
  giá và MOQ, dòng giá mơ hồ, người duyệt không có quyền giá, sai schema). Grader
  `supply_chain.step_preparation` thêm bước `bm04`; bất biến chấm cả live: ô mô hình giữ có
  trích dẫn nằm trong bằng chứng được cho xem và số có trong trích dẫn.

Mutation (control xanh trước; `test_bm04_prefill.py` + dataset 1.6.0): mâu thuẫn lấy giá trị
đầu; mô hình điền ô thương mại (sống sót lần đầu: lớp gọi đã loại, thêm test domain rồi đỏ);
không kiểm trích dẫn trong bằng chứng; không kiểm số trong trích dẫn; không kiểm chữ trong trích
dẫn; mâu thuẫn giá in trích dẫn; không che giá trong trích dẫn khác; bằng chứng đưa dòng có
giá; hỏi mô hình ô thương mại; dòng mơ hồ lấy dòng đầu; ghi giá không cần `commercial.write`;
BM04 không giá xóa giá trước; thiếu ô bắt buộc vẫn ghi hồ sơ; duyệt không ghi hồ sơ; không báo
mâu thuẫn; không báo ô bắt buộc; ô mô hình không qua kiểm. 17/17 đỏ.

`reviewing-feature-security`: (1) không bảng mới; đọc qua cổng RLS, hồ sơ tenant khác và bản
đọc workspace khác: 0 lượt gọi; (2) giá ghi vào hồ sơ chỉ khi người duyệt có
`commercial.write`, kiểm ngay chỗ ghi; quyền duyệt là scope duty đóng dấu, không đổi; (3) tầm
mới của AI: đọc ô BM04 từ bằng chứng không giá, không đổi trạng thái; (4) mô tả báo giá là dữ
liệu trong `<input>`, ca chèn lệnh đòi giá 0,01 USD và duyệt; đầu ra qua schema rồi kiểm dẫn
chứng; (5) audit hồ sơ cùng giao dịch, mỗi ô nêu chứng từ và trích dẫn; (6) một lượt gọi chỉ
khi bản nháp mới cần, plan kiểm trước; gọi hỏng thì không có lời của mô hình; thiếu quyền giá
thì không giá (đóng). Không đổi image hay phụ thuộc.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): một vòng thật qua runner Postgres
(hồ sơ vào bước 7 → lane chuẩn bị BM04 → approval `step_proposal.complete_profile` → duyệt web
và Zalo → chứng từ BM04 `ai_prepared` + phiên bản `product_profiles` trong một giao dịch),
`test_step_preparations.py` mở rộng cho `apply_approved(profile=…)` (rollback cả hồ sơ khi
phiên bản trùng), `test_commercial.py` cho `insert_product_profile` dùng chung.

Chưa làm, ghi lại: BM04 riêng của Elmich (QE-21: schema và mẫu thật là override); ô của biên
bản đánh giá vào BM04 bằng code (hôm nay chỉ qua mô hình, có dẫn chứng); mâu thuẫn giữa nhiều
file cùng loại (chỉ đọc bản mới nhất mỗi loại).
