# 05 — Quyết approval bằng Zalo sau khi xem trên cổng, với mã dùng một lần

Status: ready-for-agent
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
