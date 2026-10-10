# 03 — Profile SP (BM04) và thống nhất SP với NCC (bước 7–8)

Status: resolved
Blocked by: .claude/plans/supply-chain/stage-1/issues/02-bod-review-step-6.md
Area: supply-chain

## Mục tiêu

R&D và Cung ứng hoàn tất BM04; TP Cung ứng xác nhận SP đã thống nhất với NCC, kèm email
của NCC; hồ sơ sang tạo mã hàng.

## Việc cần làm

1. **Trạng thái mới** `supplier_confirmation`, `item_coding`. Hành động
   `complete_profile` (`profile_in_progress` → `supplier_confirmation`, duty `rnd`) và
   `confirm_with_supplier` (`supplier_confirmation` → `item_coding`, duty
   `supply_lead`).
2. **Chứng từ bắt buộc:** `complete_profile` cần `product_profile_bm04`;
   `confirm_with_supplier` cần `supplier_confirmation_email`; cả hai thuộc đúng hồ sơ.
3. **Duty và vai:** scope `supply_chain.duty.supply_lead`; vai `sc_supply_lead` (TP Cung
   ứng); policy duty gán hai hành động.
4. **Web:** hai hành động trên trang chi tiết, tải chứng từ ngay trong luồng.

## Tiêu chí chấp nhận

- [x] Unit: chuyển đúng; sai trạng thái bị từ chối; thiếu chứng từ 409 nêu loại.
- [x] Duty: `sc_rnd` không `confirm_with_supplier`; `sc_supply_lead` không
      `complete_profile` (403).
- [x] **Test âm route:** hai hành động trên hồ sơ tenant khác hoặc workspace khác trả 404;
      chứng từ của tenant khác không thỏa điều kiện (không thể gắn, xem lát D).
- [x] `make ci` xanh (một lệnh, 2026-10-07).

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 7–8; mục 5 điểm 1.
- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 2 hàng 7–8 ("SLA key only"), mục 3 hàng `PROFILE_IN_PROGRESS`,
  `SUPPLIER_CONFIRMATION`.

## Comments

- Giả định: bước 8 là xác nhận trong ứng dụng kèm file email, không đọc hộp thư (QE-09).
- SLA `bm04` và `supplier_confirmation` được gắn vào hai trạng thái ở ticket 06.

- 2026-10-07 (lát S3, quyết định tạm của implementer; Đạt ủy quyền quyết các điểm mở).
  Đoạn sửa đổi ở
  [ADR 0016](../../../../../packages/python/dw_supply_chain/docs/adr/0016-e6-stage-1-is-a-product-development-case-inside-dw-supply-chain.md)
  (mục "Sửa đổi 2026-10-07"). Mỗi điểm ghi chỗ nó nằm trong code và test giữ nó.
    1. **Trạng thái, hành động:** `supplier_confirmation`, `item_coding`;
       `complete_profile` (`profile_in_progress` → `supplier_confirmation`),
       `confirm_with_supplier` (`supplier_confirmation` → `item_coding`), cả hai không lý
       do. `item_coding` ở S3 chỉ có bước ngoại lệ và hủy (S4 thêm mã hàng, SKU). Ba trạng
       thái 7–9 tạm dừng, tiếp tục, hủy được như các bước đang chạy khác.
       `ProductDevelopmentCase.complete_profile/confirm_with_supplier`; test
       `test_steps_seven_and_eight_move_on_and_record_their_paper`,
       `test_available_actions_are_the_states_own_steps_plus_the_exceptions`.
    2. **Chứng từ bắt buộc, đúng hồ sơ, đúng bước:** BM04 (`product_profile_bm04`) cho bước
       7, email xác nhận của NCC (`supplier_confirmation_email`) cho bước 8; thiếu, của hồ
       sơ khác, của PO, sai loại, của tenant hay workspace khác, hoặc tải lên trước khi hồ
       sơ tới bước đó đều là một 409 nêu loại thiếu (`document_refusal`). Domain
       `_step_document` là chủ duy nhất (cùng hàm với chứng từ của vòng mẫu, mốc khác).
       Test `test_steps_seven_and_eight_refuse_paper_that_is_not_this_steps` (12 tham số),
       `test_steps_seven_and_eight_refuse_paper_that_is_not_theirs_with_409` (handler).
    3. **Mốc "tải lên sau khi vào trạng thái":** `stage_entered_at`, repository đọc từ
       lịch sử (dòng mới nhất vào trạng thái hiện tại mà không phải `resume`). Đọc theo
       nghĩa "lúc hồ sơ TỚI bước": tạm dừng rồi tiếp tục không tới lại bước, nên BM04 tải
       lên trước khi tạm dừng vẫn hợp lệ; BM04 soạn khi mẫu còn test thì không. Một bước
       tiến trong bộ nhớ xóa mốc (chưa lưu thì không chứng từ nào qua). Không thêm cột vào
       `product_dev_cases`: lịch sử đã là chủ của sự kiện này. Test integration
       `test_a_bm04_uploaded_before_bgd_approved_is_refused_by_its_upload_time`,
       `test_a_bm04_uploaded_before_a_pause_still_counts_after_the_resume`; unit
       `test_a_step_reached_by_a_step_in_this_instance_forgets_the_old_bound`.
    4. **Chứng từ trên dòng lịch sử:** cột `document_id` của
       `product_dev_case_state_transitions`, FK ghép tới `case_documents` của CHÍNH hồ sơ
       (cùng tenant, workspace), `ON DELETE NO ACTION`, index riêng; CHECK
       `ck_product_dev_case_state_transitions_document_steps` (hai bước này có, dòng khác
       không). Repository đổi từ chối của FK, CHECK thành cùng 409 theo tên ràng buộc.
       `GET .../transitions` trả `document_id`. Test
       `test_a_steps_paper_must_be_a_document_of_its_own_case` (hồ sơ khác, workspace
       khác), `test_only_steps_seven_and_eight_carry_a_paper_and_both_must`,
       `test_a_database_refusal_of_a_steps_paper_is_a_409_naming_its_type`,
       `test_a_document_a_step_was_taken_on_cannot_be_deleted_on_its_own`; offboarding
       (`test_offboarding_purges_product_cases_rounds_and_documents_of_one_tenant`) nay
       đi tới `item_coding` và vẫn xóa sạch một tenant.
    5. **Policy duty 1.1.0, override cũ vẫn hợp lệ:** file đổi tên
       `supply_chain_product_action_duties@1.1.0.yaml` (`complete_profile: rnd`,
       `confirm_with_supplier: supply_lead`); `PRODUCT_ACTION_DUTIES_POLICY_FILE` là chủ
       tên file, test đọc hằng đó. `SupplyChainProductActionDuties.from_stored`: override
       lưu ở 1.0.0 mà không nêu hai bước thêm sau (`STEPS_ADDED_AFTER_1_0_0`) lấy duty của
       nền tảng cho ĐÚNG hai bước đó; mọi lựa chọn khác của tenant giữ nguyên; override
       1.0.0 thiếu một bước đã có từ trước vẫn bị từ chối (fail closed); override khai
       1.1.0 và mọi PUT mới phải nêu đủ. `resolve_product_action_duties` dùng nó (một chỗ
       cho cả bước và `GET /product-action-duties`). Test
       `test_a_1_0_0_override_takes_the_platforms_duty_for_steps_it_predates`,
       `test_only_the_steps_a_1_0_0_override_predates_are_filled`,
       `test_an_override_written_at_1_1_0_must_name_steps_seven_and_eight`,
       `test_an_override_stored_before_steps_seven_and_eight_still_lets_them_be_taken`.
       Manifest phát hành sinh lại (`sha256:27dfe4917d87…`, history thêm
       `manifest-27dfe4917d87.json`).
    6. **Duty và vai:** `CaseDuty.SUPPLY_LEAD`; vai `sc_supply_lead` (TP Cung ứng) =
       `sc_viewer` + `supply_chain.duty.supply_lead` + `supply_chain.document.write` (tải
       email lên), không `duty.exceptions` (như `sc_rnd`). Duty ở phía vận hành của
       `sod_sc_rules_vs_operations` (vẫn năm luật). Chưa có luật R&D–TP Cung ứng hay
       Cung ứng–TP Cung ứng (QE-16). Bước 7 thuộc `rnd` dù bảng của Elmich ghi "R&D và
       Cung ứng": một người bấm, công ty ghi đè được. Test
       `test_the_supply_lead_holds_exactly_the_viewer_its_duty_and_the_document_write`,
       `test_rnd_cannot_confirm_with_the_supplier`,
       `test_the_supply_lead_cannot_complete_the_bm04`,
       `test_step_seven_or_eight_by_the_wrong_duty_is_403` (route).
    7. **Migration** `3fc6599ecd5e` (`down_revision = d4048e50d4a3`, một head): nới năm
       CHECK, thêm cột, FK, CHECK, index, vai, scope SoD. Downgrade TỪ CHỐI khi còn hồ sơ
       ở hoặc dừng từ hai trạng thái mới, dòng lịch sử nêu chúng, hay membership giữ
       `sc_supply_lead`. Đã chạy upgrade → downgrade -1 → upgrade trên DB local.
    8. **Web:** trang chi tiết đưa hai bước (nhãn "Hoàn tất BM04", "Đã thống nhất với
       NCC"; trạng thái "Chờ thống nhất với NCC", "Đang tạo mã hàng" theo glossary), khóa
       kèm lý do bằng chữ ("cần nhiệm vụ TP Cung ứng"). Form của bước lọc chứng từ theo
       `documents_since` server trả kèm từng lựa chọn (bỏ bản sao luật "vòng mẫu" ở trang;
       không có mốc thì không đưa chứng từ nào) và **tải chứng từ ngay trong form**: file
       vừa tải được chọn cho bước, thẻ Chứng từ làm mới. Upload dùng chung
       `useCaseDocumentUpload` (khóa idempotency một lần bấm) với thẻ Chứng từ; người
       thiếu `document.write` thấy nút khóa kèm lý do. Server vẫn quyết tất cả.
    9. **Persona:** Tuấn (`dev|tuan.le`, `sc_supply_lead`) ở workspace chính của Alpha;
       `keycloak_dev_users.py` đọc từ DB.
    10. **QE-09 giữ nguyên:** xác nhận trong ứng dụng kèm file email (EML, MSG hoặc PDF
        theo danh sách loại file của lát D), không đọc hộp thư.

- 2026-10-07 (lát S3) **Kiểm tra đã chạy** trên `feat/elmich-a-d-s1`:
    - `make ci` (một lệnh): exit 0, ">> local CI gate passed". Trong đó `make lint` xanh (eslint chỉ cảnh báo ở file ngoài lát); `make typecheck`: mypy
      sạch 503 file, tsc xanh 4 gói; `make test-unit`: 2237 passed, 3 skipped;
      `make test-architecture`: 9 kept, 0 broken, declared-dependency 12 gói,
      `verify_invariants.py` ok; `make test-contract`: 5 passed; `make eval-smoke`:
      platform_smoke 4/4, supply_chain_smoke 30/30; `make release-manifest-check` OK;
      `make test-hooks`: 76 passed.
    - Integration (`.env` sourced, infra của repo này): `dw_supply_chain` 219 passed;
      `dw_platform` 226 passed; `apps/worker` + `apps/api` 31 passed.
    - Vitest `product-cases.test.tsx` + `labels.test.ts`: 56 passed (24 + 32).
    - `make generate-contracts`: `openapi.json` và `supply-chain.d.ts` sinh lại, LF.
- 2026-10-07 **Mutation** (script trong scratchpad; control xanh trước, khôi phục và kiểm
  `restored=True` sau mỗi lần), cả 14 đều ĐỎ: bỏ so thời điểm tải lên; bỏ so tenant của
  chứng từ; mốc của bước 7–8 lấy theo vòng mẫu; không xóa mốc khi bước tiến trong bộ nhớ;
  BM04 không bắt buộc; policy giao bước 8 cho `rnd`; điền override cho mọi version; điền
  mọi bước thiếu; không điền override cũ; handler bỏ kiểm workspace; `resume` tính là
  tới bước (integration); không ghi `document_id` lên lịch sử (integration); không đổi
  từ chối FK thành 409 (integration); trang bỏ lọc `documents_since` (vitest, 3 test đỏ).
- 2026-10-07 **reviewing-feature-security:** (1) tenant: cột mới trong bảng đã RLS FORCE,
  mọi câu qua `tenant_session`; chứng từ đọc dưới RLS của người gọi; FK ghép có
  workspace; test âm tenant, workspace khác (`get` None, `save` từ chối, FK từ chối).
  (2) authorization: duty kiểm ở handler trước khi đọc hồ sơ hay chứng từ; 403 ở handler
  và route; nút khóa chỉ là gợi ý. (3) autonomy: không tool, không model, không run mới.
  (4) nội dung không tin cậy: file không vào mô hình; tên file hiện dạng text. (5) audit:
  bước, dòng lịch sử, chứng từ của bước và audit cùng một giao dịch; xóa chứng từ một
  bước đã dùng bị FK từ chối (test khẳng định tên FK). (6) mặc định: override hỏng vẫn
  từ chối; điền override cũ chỉ cho hai bước mới và chỉ bằng duty của nền tảng; mốc
  không biết thì không chứng từ nào qua (domain và trang). Không đổi Dockerfile,
  lockfile hay compose.
- **Còn mở:** SLA `bm04`, `supplier_confirmation` gắn ở ticket 06; luật tách nhiệm cho TP
  Cung ứng chờ QE-16; Playwright viewports (lát W) chưa chạy cho hai form mới.
