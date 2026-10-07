# 05 — Quyết approval bằng Zalo sau khi xem trên cổng, với mã dùng một lần

Status: resolved
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/04-chat-proposal.md, .claude/plans/supply-chain/zalo-channel/issues/02-channel-delivery.md, .claude/plans/supply-chain/approval-decider-scope/issues/01-required-scope.md, .claude/plans/supply-chain/stage-1/issues/02-bod-review-step-6.md
Area: supply-chain

## Mục tiêu

BGĐ (bước 6) và người ký (bước 9) nhận tin Zalo có tóm tắt và liên kết, mở hồ sơ trên
cổng, thấy mã `4821`, rồi trả lời `DUYỆT 4821 <nhận xét>` hoặc `TỪ CHỐI 4821 <lý do>`
trong Zalo. Server chỉ nhận khi mọi kiểm của
[ADR 0014](../../../../../docs/adr/0014-e4-decisions-on-zalo-after-a-portal-view.md)
đúng; kiểm nào hỏng thì từ chối và bot nói vì sao. Quyết trên web không đổi. Phần biên
nhận, mã và dịch vụ quyết là code nền tảng, ứng viên đưa ngược; phần trả phiên bản hồ sơ
nằm trong `dw_supply_chain`.

## Việc cần làm

1. **Migration nền tảng** (id hex ngẫu nhiên), RLS ENABLE và FORCE hình workspace chuẩn,
   grant cho `dw_app` trong migration, có trong `test_privileges.py`:
    - `platform.approval_view_receipts`: `id`, `tenant_id`, `workspace_id`, `approval_id`
      (FK `(tenant_id, approval_id)` tới `approval_requests` `ON DELETE CASCADE`, có
      index), `user_id` (FK `platform.users` `ON DELETE CASCADE`, có index),
      `approval_version int`, `subject_version text`, `viewed_at timestamptz`. Bảng con
      giới hạn theo approval, không partition.
    - `platform.approval_decision_codes`: `id`, `tenant_id`, `workspace_id`,
      `approval_id`, `user_id`, `receipt_id` (FK `ON DELETE CASCADE`, có index),
      `code_hash bytea` (HMAC-SHA256 với `DW_APPROVAL_CODE_SECRET` trên
      `approval_id || user_id || mã`), `expires_at`, `used_at NULL`, `revoked_at NULL`,
      `created_at`; UNIQUE riêng phần `(tenant_id, approval_id, user_id) WHERE used_at IS
NULL AND revoked_at IS NULL` (một mã mở mỗi người mỗi approval).
    - `platform.approval_decisions` thêm `channel text NOT NULL DEFAULT 'web'` CHECK
      `('web','zalo')` (dòng cũ đúng là web).
    - Tìm mã còn mở của một người qua mọi tenant (mã là theo người, và người có thể là
      thành viên nhiều tenant) bằng cơ chế baseline đã có, không bằng hàm SECURITY
      DEFINER: thêm policy `approval_decision_codes_self_select` (`FOR SELECT`, điều
      kiện `user_id` bằng `app.principal_id`, viết như baseline) bên cạnh policy hình
      workspace, như `memberships_self_select` (`0001_platform_baseline.sql:818`). Bot đặt
      `app.principal_id` theo người đã giải từ chat (Z4 bước 3), đọc mã, rồi đặt
      `app.tenant_id`, `app.workspace_id` theo dòng mã trước khi tiêu mã và quyết, nên câu
      `UPDATE` tiêu mã chạy dưới policy tenant và workspace. `test_rls_coverage.py` xanh
      (principal là setting tin được); test âm: principal A không đọc mã của B, và không
      đặt principal thì đọc 0 dòng.
    - Bảng mặt phẳng danh tính `platform.approval_code_failures` (`user_id` FK cascade có
      index, `occurred_at`) đếm lần sai; dọn sau 1 ngày ở lane `retention`.
2. **Port phiên bản hồ sơ** `ApprovalSubjectVersionPort` (nền tảng khai báo, theo tiền
   tố `approval_type`, đăng ký ở composition root như `strict_approval_prefixes`):
   `dw_supply_chain` thỏa cho `supply_chain.product_action.` bằng `version` của hồ sơ
   phát triển ghi trong payload approval. Không có ai trả phiên bản thì không cấp mã.
3. **Xem trên cổng:** `POST /approvals/{id}/view` (`Idempotency-Key` không cần: mỗi lần
   xem là một biên nhận). Ghi biên nhận; cấp mã chỉ khi approval `pending`, người xem
   giữ `approvals.decide` và `required_scope`, không phải người yêu cầu, có liên kết
   Zalo, và loại approval có port phiên bản; thu hồi mã mở cũ trong cùng giao dịch. Trả
   `{code, expires_at, command_approve, command_reject}` hoặc lý do không cấp. Mã sinh
   bằng `secrets`, không trùng với mã mở khác của cùng người.
4. **Web (antd):** trang approval (và khối approval trên trang hồ sơ) gọi `view` khi mở,
   hiện mã, giờ hết hạn theo `lib/dates.ts`, đúng câu lệnh cần gõ (có nhận xét nếu loại
   nghiêm) với nút chép; không cấp mã thì nói vì sao. Đứng ở 320 px.
5. **Tin Zalo:** thông báo "có việc cần duyệt" của S2 và S4 đi qua hộp thư kênh (Z2) với
   tóm tắt (mã đề xuất, tên SP, loại duyệt, tenant, workspace) và liên kết tuyệt đối tới
   trang approval. Không có mã.
6. **Bộ đọc lệnh** (tất định, không mô hình): `^(DUYỆT|TỪ CHỐI)\s+(\d{4})(?:\s+(.+))?$`
   sau chuẩn hóa NFC, hoa thường và dấu. Đăng ký trước mọi lệnh khác ở bộ định tuyến
   của Z4. Tin bắt đầu bằng "duyệt" mà không khớp văn phạm nhận câu hướng dẫn, không đi
   tới mô hình.
7. **Dịch vụ quyết từ kênh** `ChannelApprovalDecisionService`: giải chat id thành
   `user_id`; khử trùng theo id tin (Z4); đọc mã mở qua hàm ở bước 1, so HMAC bằng
   `hmac.compare_digest`; dựng AccessContext cho `(user_id, tenant, workspace của mã)`
   với trần `{approvals.decide, required_scope}` (Z4 bước 3); kiểm người yêu cầu (mọi
   loại); kiểm biên nhận: `approval_version` và `subject_version` bằng hiện tại; rồi gọi
   `ApproveAndResumeService.decide(..., channel="zalo", guard=...)`.
8. **`decide` nhận kênh:** tham số `channel` (`web` mặc định cho web) ghi vào dòng quyết
   định, vào audit, và vào `RunContext.channel` khi resume (`approval_flow.py:155`, nay
   cứng `"web"`); tham số `guard` tùy chọn chạy trong cùng unit of work trước khi ghi
   quyết định, ở đây là câu tiêu mã có điều kiện:

    ```sql
    UPDATE platform.approval_decision_codes
       SET used_at = now()
     WHERE id = :code_id AND user_id = :uid AND used_at IS NULL
       AND revoked_at IS NULL AND expires_at > now()
    RETURNING id
    ```

    0 dòng thì từ chối và giao dịch lùi.

9. **Lối từ chối, mỗi lối một câu của bot:** mã sai hoặc của người khác (cùng một câu:
   "Mã không đúng hoặc đã hết hạn"); mã hết hạn; mã đã dùng; hồ sơ hoặc approval đổi sau
   khi xem ("mở lại liên kết để xem bản mới và lấy mã mới"); thiếu quyền; người yêu cầu;
   approval đã quyết (nói lúc nào, kênh nào); thiếu nhận xét với loại nghiêm; từ chối
   thiếu lý do; sai 5 lần trong 15 phút (thu hồi mọi mã mở của người đó).
10. **Audit:** quyết định ghi `channel`, `approval_version`, `subject_version`,
    `receipt_id`, `code_id`, id tin Zalo; mỗi lần từ chối ghi `approval.channel_decision_refused`
    với mã lý do, không ghi mã người gõ.

## Tiêu chí chấp nhận

- [ ] Đường thành công: xem trên cổng, `DUYỆT <mã> <nhận xét>` từ Zalo của đúng người:
      approval `approved`, dòng quyết định `channel='zalo'`, run tiếp tục từ checkpoint
      với `channel="zalo"`, một audit event mang đủ trường của bước 10.
- [ ] **Mỗi lối từ chối ở bước 9 có một test âm**: approval vẫn `pending`, run không tiếp
      tục, không có dòng quyết định, mã không bị tiêu (trừ khi đã dùng), có một audit
      event từ chối, bot gửi đúng câu của lối đó.
- [ ] **Chưa xem:** người có quyền gõ một mã hợp lệ của người khác hoặc đoán mã khi chưa
      từng xem: từ chối. Mutation: bỏ kiểm `user_id` của mã thì test đỏ.
- [ ] **Phiên bản cũ:** xem, rồi hồ sơ đổi (tăng `version`), rồi gõ mã: từ chối. Mutation:
      bỏ so `subject_version` thì test đỏ.
- [ ] **Phát lại:** cùng lệnh gửi hai lần (hai tin khác id) và cùng update tới hai lần (cùng
      id), hai giao dịch song song trên Postgres thật: đúng một quyết định. Mutation: đổi
      câu tiêu mã thành đọc rồi cập nhật thì test đỏ.
- [ ] **Người yêu cầu:** cổng không cấp mã cho người yêu cầu; với một loại approval không
      nghiêm, người yêu cầu có mã (chèn thẳng vào bảng trong test) vẫn bị dịch vụ Zalo từ
      chối. Mutation: bỏ kiểm người yêu cầu ở dịch vụ thì test đỏ.
- [ ] **Thiếu quyền lúc quyết:** gỡ `required_scope` khỏi vai sau khi xem: từ chối.
- [ ] **Xuyên tenant:** mã của tenant A gõ từ chat liên kết với người chỉ thuộc tenant B:
      từ chối với câu "mã không đúng"; không có truy vấn nào ở tenant A ngoài hàm SECURITY
      DEFINER. **Test âm RLS** cho hai bảng mới; `test_rls_coverage.py` xanh.
- [ ] **Mã không đi ra ngoài:** sau khi xem, giá trị mã không có trong
      `platform.notifications`, `platform.channel_deliveries`, audit event, log; trong DB
      chỉ có `code_hash`.
- [ ] **Không mô hình:** test kiến trúc: chỉ `ChannelApprovalDecisionService` (và route web
      sẵn có) với tới `ApproveAndResumeService.decide`; không module nào của Z4, Z6 hay
      workflow với tới nó. Câu tự do "duyệt hết", "approve <id>", "DUYỆT" không mã không
      sinh quyết định.
- [ ] Loại approval không có port phiên bản: `view` không cấp mã, trang nói vì sao.
- [ ] Một approval dạng `signoff` của S4 (payload tạo trong test) dùng cùng port phiên bản
      và cùng các kiểm.
- [ ] Vitest trang approval: hiện mã, giờ hết hạn, câu lệnh đúng loại; không cấp mã thì hiện
      lý do; 320 px.
- [ ] `make ci` xanh; integration của `dw_platform`, `dw_agent_runtime`, `dw_supply_chain`
      xanh; openapi và `platform.d.ts` sinh lại.

## Nguồn

- `docs/products/elmich/process.md` mục 3.2, bước 6 và 9; mục 4 hàng 6 và 9.
- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` mục 1 (`DecisionEngine.try_text`,
  `zalo_approval_notifier.py` `_reply_hint`), mục 3 ("Concept only").
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 8 điểm 5 (không có nút,
  phải đọc chữ), mục 10 điểm 1 (nay được ADR 0012 sửa).
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4 "Platform gap" (`required_scope`).
- Trên `main`: `approval_flow.py:96-155` (`decide`, `channel="web"` dòng 155),
  `approval_flow.py:46-61` (luật nghiêm), `domain/approval.py:44-58` (`version`).

## Comments

- Lệch có chủ ý với câu chữ của Đạt: loại approval nghiêm (`supply_chain.product_action.`)
  đòi nhận xét, nên lệnh duyệt là `DUYỆT 4821 <nhận xét>`; sàn nền tảng không bị kênh làm
  yếu (ADR 0014 điểm 6).
- 2026-10-05 (từ lát A, `approval-decider-scope/issues/01`): approval nay có thể mang
  `required_scope` (ADR 0020), và chỉ `ApproveAndResumeService.decide` kiểm nó, trước mọi
  ghi và trước `runner.resume`. Z5 phải quyết qua đúng `ApproveAndResumeService.decide`,
  không ghi `approval_decisions` hay gọi `runner.resume` theo đường riêng; nếu không, một
  lệnh `DUYỆT` trên Zalo của người có `approvals.decide` mà thiếu dấu (vd thiếu
  `supply_chain.approve.bod`) sẽ qua mặt luật "chỉ BGĐ quyết".
- 2026-10-07, **resolved (Z5), theo ADR 0014 và sửa đổi 2026-10-06 (QO-7).** Đạt giao
  quyết các điểm mở; quyết định tạm, cách đọc an toàn nhất, ghi ở đây và ở sửa đổi
  2026-10-07 của ADR 0014. Sửa đổi 2026-10-06 thay câu chữ của ticket ở các điểm: mã 6 số,
  10 phút, sai 5 lần khóa mã, lệnh `DUYỆT <mã>` / `KHÔNG <mã> <lý do>`, nhận xét nhập trên cổng.
    - **Đã làm.** Migration `dbb8c3359981` (down `4a865a1c97aa`): `approval_view_receipts`
      (FK `(tenant_id, workspace_id, approval_id)` tới UNIQUE mới trên `approval_requests`,
      CASCADE), `approval_decision_codes` (FK ghép tới biên nhận `(receipt_id, tenant,
workspace, approval, user)`, `code_hash` 32 byte, `comment` ≤ 2000, `failed_attempts`
      0..5, CHECK khóa ở 5, CHECK `revoked_reason`, UNIQUE một phần một mã mở mỗi người mỗi
      approval), cả hai RLS ENABLE+FORCE hình workspace chuẩn, thêm
      `approval_decision_codes_self_select` (FOR SELECT theo `app.principal_id`);
      `approval_decisions.channel` (`web`/`zalo`, mặc định `web`); grant trong migration
      (`dw_app`: biên nhận SELECT/INSERT; mã SELECT/INSERT + UPDATE 4 cột; không DELETE);
      `platform.prune_approval_decision_codes()` SECURITY DEFINER, lane
      `approval_codes_retention` (1 ngày). Nền tảng: `dw_platform.application.approval_codes`
      (`DecisionCodeKey` HMAC-SHA256, `ApprovalSubjectVersions`, các port),
      `adapters/persistence/approval_codes.py` (`SqlApprovalCodeStore`,
      `SqlDecisionCodeLedger` trong UoW, `SqlApprovalCodeRetention`),
      `dw_agent_runtime.approval_codes` (`ApprovalViewService`, `CodeAdmission`),
      `dw_agent_runtime.channel_decisions` (văn phạm, `ChannelApprovalDecisionService`,
      `ChannelDecisionCommand`), `ApproveAndResumeService.decide(channel=, admission=)`,
      route `POST /api/v1/approvals/{id}/view`, `approval_link`. Supply Chain:
      `ProductCaseApprovalSubject` (phiên bản = `version` của hồ sơ theo
      `product_dev_case_id`), thông báo BGĐ dẫn `/approvals/<id>?workspace=<ws>`. Web: trang
      `/approvals/[id]` (antd), khối BGĐ trên trang hồ sơ dẫn tới đó, thẻ trên `/approvals`
      dẫn tới trang của nó, `ApprovalStatusTag` dùng chung. Worker: `ApproveAndResumeService`
      riêng trên runner của graph BGĐ (dùng chung với lane reconcile,
      `build_product_review_runner`), lệnh quyết đăng ký đầu tiên
      (`build_channel_decision_command`). `DW_APPROVAL_CODE_SECRET` ở `.env.example`, compose,
      settings API và worker.
    - **Q1, nhận xét và cấp mã.** Mở trang ghi biên nhận (không mã). Bấm "Lấy mã" gửi nhận xét
      với `issue_code`: biên nhận mới + mã mới gắn nhận xét, mã mở cũ bị thu hồi (`reissued`)
      cùng giao dịch. Loại nghiêm mà nhận xét rỗng: không cấp (`comment_required`).
      `KHÔNG`: nhận xét cổng + xuống dòng + lý do trong tin.
    - **Q2, văn phạm.** `DUYỆT <6 số ASCII>` đúng nguyên tin (thừa chữ, sai độ dài, chữ số
      toàn chiều rộng: câu hướng dẫn); `KHÔNG <6 số> <lý do>`; "không" chỉ là lệnh khi theo sau
      là số, vì "không" mở nhiều câu thường (Z4b đọc tiếp); "duyệt..." bất kỳ dạng nào không
      bao giờ tới mô hình; `TỪ CHỐI` bỏ (sửa đổi 2026-10-06).
    - **Q3, sai 5 lần.** Tin không nêu approval, nên một lần sai tính cho **mọi** mã đang mở
      của người gửi (mọi tenant, mỗi dòng dưới tenant/workspace của nó); mã tới 5 bị khóa
      (`revoked_reason = 'locked'`). Thay bảng `approval_code_failures` của ticket (không tạo):
      bộ đếm nằm trên dòng mã, CHECK 0..5. Lần sai không khớp mã nào của người gửi thì không
      có tenant để ghi audit: chỉ log (không có chữ số).
    - **Q4, câu trả lời.** Không khớp (gõ nhầm, đoán, mã người khác, tenant khác) = một câu
      "Mã không đúng hoặc đã hết hạn." Mã của chính người gửi: hết hạn, đã dùng, đã thay bằng
      mã mới, bị khóa — mỗi trạng thái một câu (không lộ gì về người khác). Hai mã mở trùng
      chữ số (cấp mã tránh, nhưng vẫn có thể) thì từ chối như "đã đổi", không đoán.
    - **Q5, workspace.** Bộ định tuyến Z4a không đổi: vẫn giải workspace đã chọn trước mọi lệnh
      (nhiều workspace mà chưa chọn thì được nhắc chọn, như Z4a). Lệnh quyết khai trần
      `{approvals.decide}`, chỉ dùng context đó để lấy người; context quyết dựng lại cho
      workspace **của mã** với trần `{approvals.decide, required_scope}`, không role.
    - **Q6, `guard` thành `admission`.** `DecisionGuard` đã là luật theo loại; tham số mới là
      `DecisionAdmission` theo từng quyết định, chạy sau mọi kiểm của `decide`, trước mọi ghi,
      trong cùng UoW; từ chối thì lùi. Quyết định được admit ghi audit
      `approval.channel_decided` cùng giao dịch (`channel`, `approval_version`,
      `subject_version`, `receipt_id`, `code_id`, `message_id`, `chat_reference`,
      `decision_id`, `outcome`); quyết trên web không thêm audit (giữ nguyên).
    - **Q7, worker quyết.** Worker dựng `ApproveAndResumeService` của mình trên runner chứa
      graph BGĐ; `register_product_approvals` thêm tiền tố nghiêm và port phiên bản như
      `wiring.py` của API. `memory.` không đăng ký ở worker: không có port phiên bản thì không
      bao giờ được quyết qua chat. Run của graph mà worker không chứa: `decide` từ chối
      ("Chưa quyết được qua Zalo"), đóng.
    - **Q8, khóa.** `DW_APPROVAL_CODE_SECRET` ≥ 16 byte; trống thì cổng không cấp mã
      (`channel_off`) và bot trả "chưa bật", không gì tới mô hình.
    - **Q9, liên kết.** `approval_link` (một chủ) = `/approvals/<id>?workspace=<ws>`; trang đổi
      sang workspace đó nếu người xem là thành viên, không thì "không tìm thấy". Tiêu đề thông
      báo giữ nguyên (chỉ tiêu đề ra Zalo, QE-20 của Z2). Keycloak `login-required` quay về
      đúng URL sau đăng nhập.
    - **Q10, còn lại có chủ ý.** Phiên bản hồ sơ đọc ngoài giao dịch của `decide` (phiên bản
      approval thì kiểm lại trong giao dịch); hồ sơ đổi giữa hai bước thì graph tự thấy và
      `superseded`. 320 px: vitest (jsdom, matchMedia điện thoại, lệnh `break-all`) — chưa đo
      trên trình duyệt thật (viewport Playwright vẫn nợ, ticket W).
    - **Test.** Unit: dw_agent_runtime `test_channel_decision_grammar.py` 32 (văn phạm, HMAC,
      registry, lệnh, kiến trúc: chỉ route web và `channel_decisions.py` gọi `.decide(approve=)`),
      `test_approval_flow.py` +5 (kênh, admission); API `test_approvals_endpoint.py` +6 (route
      view). Integration: dw_platform `test_approval_codes.py` 18 (RLS principal/tenant/
      workspace, tiêu mã chỉ chủ và một lần, hai giao dịch đua, hết hạn, khóa ở 5, thu hồi khi
      cấp lại, dọn, CHECK, FK) + privileges 1 + rls coverage (lý do policy principal);
      dw_agent_runtime `test_channel_decisions.py` 20 (đường thành công + audit, `KHÔNG`, mã
      không rời response, mã người khác, tenant khác, approval khác, chưa xem, hết hạn, dùng
      hai lần, cấp lại, khóa lần thứ 5, approval đổi, hồ sơ đổi, đã quyết trên web, mất scope
      sau khi xem, người yêu cầu loại không nghiêm, loại nghiêm không nhận xét, lý do không cấp,
      hai lệnh song song, run tiếp tục với `channel="zalo"`); dw_supply_chain
      `test_product_bod_review.py` +3 (BGĐ quyết qua Zalo và graph áp dụng, hồ sơ hủy sau khi
      xem, sign-off bước 9 dùng cùng port); apps/worker `test_zalo_decision_db.py` 3 (qua lane
      poll và router; khử trùng id; "duyệt hết" không tới mô hình; không khóa thì "chưa bật").
      Vitest `approval-page.test.tsx` 10.
    - **Mutation (gỡ, đỏ, khôi phục), 13/13 đỏ:** tiêu mã thành đọc rồi ghi; tiêu mã bỏ
      `user_id`; HMAC bỏ người; policy principal `USING (true)`; bỏ so phiên bản hồ sơ; bỏ kiểm
      người yêu cầu ở dịch vụ; sai không bao giờ khóa; bỏ kiểm người yêu cầu ở cổng; admission
      không được hỏi; run tiếp tục với `"web"`; lệnh quyết đăng ký sau đề xuất; CHECK khóa mở
      khi NULL (test bắt được lỗi thật lúc viết: `= 'locked'` qua được với NULL, đã sửa thành
      `IS NOT DISTINCT FROM`); tiêu mã hết hạn. Phòng thủ chồng lớp, mỗi lớp có test riêng: mã
      người khác bị chặn bởi HMAC theo người, policy principal và `user_id` trong câu tiêu.
    - **Ứng viên đưa ngược (chưa đưa):** migration `dbb8c3359981`;
      `dw_platform/application/approval_codes.py`, `adapters/persistence/approval_codes.py`,
      `uow.py`/`ports.py` (`decision_codes`), `domain/approval.py` (`channel`,
      `approval_link`), `repositories.py`, `tables.py`; `dw_agent_runtime/approval_codes.py`,
      `channel_decisions.py`, `approval_flow.py`; route `view`; trang `/approvals/[id]`,
      `ApprovalStatusTag`, client `getApproval`/`viewApproval`; test platform/runtime/API/web.
      Không đưa: `ProductCaseApprovalSubject`, `register_product_approvals`, đổi link BGĐ.
