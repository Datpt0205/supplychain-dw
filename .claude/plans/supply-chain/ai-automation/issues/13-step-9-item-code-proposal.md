# 13 — Bước 9: đề xuất mã hàng, SKU, kiểm danh mục

Status: resolved
Blocked by: .claude/plans/supply-chain/ai-automation/issues/11-step-7-bm04-prefill.md, .claude/plans/supply-chain/onboarding/issues/01-import-suppliers-catalogue-users.md
Area: supply-chain

## Mục tiêu

Vào `item_coding`, hệ thống đề xuất mã hàng theo quy tắc của tenant và SKU từ biến thể BM04, kiểm trùng với danh mục đã nạp, soạn tờ trình ký.

## Việc cần làm

1. Policy `supply_chain_item_code_rule@1.0.0` (mẫu mã; trống tới QE-11 thì không đề xuất
   mã, chỉ SKU). Mã do code sinh, không mô hình.
2. SKU từ `attributes` biến thể của BM04; số lượng dự kiến nếu có.
3. Kiểm trùng: `item_codes`, `skus`, danh mục nạp (ON-01).
4. Bản nháp `item_code_submission` (`DocumentType` mới) đi kèm `submit_for_signoff`.

## Tiêu chí chấp nhận

- [x] **Test âm:** tenant B và workspace khác cùng tenant không đọc, không ghi bảng mới; `test_rls_coverage.py`, `test_privileges.py` xanh. _(Không bảng mới. Hồ sơ tenant khác không chuẩn bị; BM04 và danh mục của workspace khác không dùng: unit và eval. `SqlCodeRegistry` theo RLS: integration viết, chưa chạy.)_
- [x] Mã trùng với danh mục đã nạp bị đánh dấu; DB vẫn từ chối trùng trong app. _(Phát hiện `item_code_taken`/`sku_code_taken` nêu nơi giữ; duyệt hỏi lại và từ chối 409; chủ thể buộc câu trả lời; UNIQUE của app không đổi, integration viết.)_
- [x] Không có quy tắc mã: không đề xuất mã (không đoán).
- [ ] `make ci` xanh; integration `dw_supply_chain` xanh; `process.md` mục 4 cột AI cập nhật. _(Phần chạy được không Docker xanh, `process.md` cập nhật; integration nợ.)_

## Nguồn

- ADR 0018; QE-11; slide 8 PoC (đối chiếu danh mục).
- `.claude/plans/supply-chain/ai-automation/spec.md`.

## Comments

**2026-10-09, lát AI-13 (agent).** Đã làm (không Docker, không mô hình):

- Policy `supply_chain_item_code_rule@1.0.0` (nền tảng `rule: null`; mẫu tiền tố + số chữ số, không
  regex) và override tạm của Elmich `EL-00001` / `EL-00001-01` tới QE-11
  (`scripts/elmich_item_code_rule_override.yaml`, seed `elmich-item-code-rule`,
  `GET|PUT /item-code-rule`, `action_duties.read|write`). Mã tiếp theo: số lớn nhất đã dùng với
  tiền tố trong ứng dụng và danh mục đã nạp, cộng một; hết chữ số thì không đề xuất.
- `DraftRecipe.ITEM_CODING` điền phiếu mã hàng (`official_item_code`, loại đã có, mẫu
  `supply_chain.official_item_code@1.0.0`; lệch ticket: không thêm `DocumentType` mới, "Mã hàng chính
  thức" đã là chứng từ của bước 9): mã hàng (của hồ sơ nếu đã có, hoặc theo quy tắc), một SKU mỗi
  biến thể BM04 (`bm04_variants`: số lượng dự kiến chỉ khi dòng kết thúc bằng số sau `:`/`=`;
  "24cm" không phải số lượng), chất liệu, kích thước, ngày trình.
- `StepCheck.CODES_FREE`: `item_code_rule_missing`, `bm04_variants_missing`, `item_code_taken`,
  `sku_code_taken` (nêu "đã cấp trong ứng dụng" / "có trong danh mục đã nạp"; mã của chính hồ sơ
  không tính). Chủ thể của đề xuất buộc phiên bản BM04 và câu trả lời về mã bị chiếm (payload
  `coding`: chỉ mã).
- Duyệt: phiếu không có mã hay SKU bị từ chối trước khi dựng gì; mã bị chiếm bị từ chối (409); rồi
  cấp mã, thêm SKU còn thiếu, `submit_for_signoff` trong một giao dịch (`version - max(1, số bước)`
  ở repository; trùng nêu mã của đúng bước), mỗi bước audit như người quyết. Lane đối soát trình
  phần ký như trước.
- Override chuẩn bị bước Elmich 1.6.0 bật bước 9; routes và dataset 1.8.0 (+9 ca bước 9, ca nào
  cũng khẳng định 0 lượt gọi mô hình).

Mutation (control xanh trước; `test_item_coding.py` + dataset 1.8.0): chủ thể không buộc mã bị
chiếm; duyệt không hỏi lại; mã tiếp theo bỏ qua danh mục (sống sót lần đầu: số của hồ sơ khác lớn
hơn; đổi ca cho danh mục giữ số lớn nhất rồi đỏ); mã của chính hồ sơ tính là trùng; số lượng không
cần dấu `:`; có mã khi không quy tắc; bỏ kiểm phiếu thiếu mã; thay mã đã cấp; không nêu SKU bị
chiếm; payload không mang mã; không phát hiện thiếu quy tắc; không phát hiện thiếu biến thể; thêm
lại SKU đã có. Eval: hồ sơ tenant khác được chuẩn bị; BM04 workspace khác được đọc; danh mục
workspace khác được đọc; số trong câu chèn lệnh thành số lượng; không biến thể mà đoán SKU. 18/18 đỏ.

`reviewing-feature-security`: (1) không bảng mới; mọi đọc theo RLS của lane, danh mục và BM04 của
workspace khác không thấy; (2) quyền duyệt là scope duty `ordering` đóng dấu như mọi đề xuất bước;
(3) không mô hình, không tool; (4) chữ trong BM04 chỉ là nhãn biến thể, không thành mã hay số
lượng; (5) ghi mã, SKU, trình ký qua đúng dispatch, một giao dịch, audit, không gửi gì ra ngoài;
(6) không quy tắc là không mã, mã bị chiếm là từ chối, thiếu BM04 là không SKU.

**Owed: not run, Docker unavailable** (không đánh dấu đạt): `tests/integration/test_item_coding.py`
(`SqlCodeRegistry` theo RLS và trừ mã của chính hồ sơ; lưu nhiều bước một lần qua kiểm phiên bản;
mã workspace khác giữ bị UNIQUE từ chối nêu mã), một vòng thật qua runner Postgres (hồ sơ vào bước
9 → đề xuất → duyệt web/Zalo → lane đối soát trình ký).
