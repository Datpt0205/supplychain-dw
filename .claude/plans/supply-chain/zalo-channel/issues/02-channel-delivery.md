# 02 — Hộp thư kênh `platform.channel_deliveries` và lane `channel_delivery`

Status: resolved
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md
Area: supply-chain

## Mục tiêu

Mỗi thông báo trong ứng dụng tới một người đã liên kết Zalo sinh đúng một lần gửi Zalo,
có trạng thái, thử lại, audit, và ngày xóa
([ADR 0013](../../../../../docs/adr/0013-e3-provider-neutral-channel-delivery-outbox.md)).
Ứng viên đưa ngược.

## Việc cần làm

1. **Migration nền tảng** (id hex ngẫu nhiên): `platform.channel_deliveries` theo ADR
   0013, bảng thường không partition (dòng được sửa tại chỗ; xem ADR 0013); ENABLE và
   FORCE RLS với hình workspace chuẩn; UNIQUE
   `(tenant_id, channel, source_key, recipient_user_id)`; CHECK cho `channel`,
   `status`; index cho lần lấy dòng tới hạn bắt đầu bằng `tenant_id`; grant cho
   `dw_app` ship cùng migration.
2. **Một cửa vào:** dòng được chèn trong cùng giao dịch với `platform.deliver_notification`
   cho mỗi người nhận có liên kết Zalo (đọc `external_identities` với `issuer='zalo'`).
   Không có đường thứ hai tạo dòng.
3. **Hàm SECURITY DEFINER** chỉ trả danh sách tenant có dòng `pending` tới hạn (như
   `supply_chain.tenants_with_cases()`), EXECUTE cho `dw_app`.
4. **Lane `channel_delivery`** (`ConsumerRegistry.register`, interval từ
   `DW_WORKER_CHANNEL_DELIVERY_INTERVAL_SECONDS`: thêm trường vào `WorkerSettings` và
   bỏ dấu `#` của dòng đã có sẵn trong `.env.example` cùng thay đổi này): mỗi tenant
   một giao dịch, `FOR UPDATE SKIP LOCKED`; tra `zalo_id_for(user_id)` lúc gửi (không
   còn liên kết thì `cancelled`); gửi qua `ChatSenderPort`; lỗi vĩnh viễn (chat không
   tồn tại, bị chặn) thì `failed`, lỗi tạm thời thì `attempts + 1` và `next_attempt_at`
   giãn theo cấp số, trần 5 lần; xong thì `sent` với `external_message_id`; mỗi kết
   cục ghi một audit event.
5. **Nội dung:** tên tenant và workspace, tiêu đề, liên kết tuyệt đối từ
   `DW_PUBLIC_WEB_URL` + `link` của thông báo. Không thân thông báo nào mang dữ liệu bị
   hạn chế.
6. **Dọn:** hàm `platform.prune_channel_deliveries()` (SECURITY DEFINER, không tham số,
   EXECUTE cho `dw_app`) xóa dòng cũ hơn 90 ngày, không bao giờ xóa dòng `pending`; một
   `SqlChannelDeliveryRetention` (mẫu `SqlNotificationRetention`) gọi nó từ lane
   `retention` có sẵn, đăng ký ở composition root.

## Tiêu chí chấp nhận

- [x] **Test âm RLS:** tenant B không đọc, không sửa dòng của tenant A; workspace khác
      cùng tenant không đọc được; `app.workspace_scope = 'tenant'` chỉ mở trong lane
      offboarding. `test_rls_coverage.py` xanh với bảng mới.
- [x] Hai lần `deliver` cùng `source_key` cho cùng người: một thông báo, một dòng gửi.
- [x] Người nhận không liên kết: không có dòng gửi. Gỡ liên kết trước lúc gửi: dòng
      thành `cancelled`, không gọi `send_message`.
- [x] Lỗi tạm thời rồi thành công: hai lần thử, một `sent`; lỗi vĩnh viễn: một lần thử,
      `failed`; quá trần: `failed`. Mỗi trường hợp có đúng một audit event cuối.
- [x] Mutation: bỏ điều kiện "có liên kết" ở cửa vào thì một test đỏ; bỏ cả mệnh đề
      `FOR UPDATE SKIP LOCKED` (hoặc bỏ việc kiểm lại `status = 'pending'` khi claim)
      thì test hai worker song song gửi trùng và đỏ (ghi vào Comments). Chỉ bỏ
      `SKIP LOCKED` mà giữ `FOR UPDATE` thì không đỏ được: worker thứ hai chờ khóa, READ
      COMMITTED kiểm lại `status` sau khi khóa nhả và bỏ qua dòng.
- [x] `SKIP LOCKED` có test riêng: khi worker thứ nhất đang giữ một dòng, worker thứ
      hai lấy dòng khác ngay, không bị chặn (đo bằng timeout ngắn).
- [x] Dọn: dòng cũ hơn 90 ngày bị xóa trừ dòng `pending`; dòng trong hạn còn nguyên;
      mutation bỏ điều kiện `status <> 'pending'` thì test đỏ.
- [x] `test_privileges.py` khẳng định grant; offboarding xuất và xóa bảng mới.
- [x] `make ci` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` mục 1 (outbox `0011_dw01_slack_notifications.py:21-104`, consumer
  `slack_approvals.py:26-172` của repo `dw` cũ), gap 3, mục 3 (bảng dùng lại).
- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 3 (`notifications.py:101-130`, `wiring.py:192`).
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 5 (gửi best-effort, không thử lại), 10 điểm 5.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 2, "Data model" và "Outbound".

## Comments

- 2026-10-07, **resolved (Z2).** Đạt giao quyết các điểm mở; quyết định tạm, an toàn nhất,
  ghi ở đây và trong sửa đổi 2026-10-07 của ADR 0013.
    - **Đã làm.** Migration `4a865a1c97aa` (down `3fc6599ecd5e`): `platform.channel_deliveries`
      (bảng thường; RLS ENABLE + FORCE, policy `tenant_isolation_channel_deliveries` hình
      workspace chuẩn; UNIQUE `(tenant_id, channel, source_key, recipient_user_id)`; CHECK
      `channel`, `status`, `attempts`, `title`, `link` (cùng regex của notifications),
      `last_error` ≤ 500, `sent` thì có `external_message_id`; index due một phần
      `(tenant_id, workspace_id, channel, next_attempt_at) WHERE status = 'pending'`; FK
      tenant/workspace/user `ON DELETE CASCADE`, mỗi FK có index; `updated_at` qua
      `touch_updated_at`). `platform.deliver_notification` thay bằng `CREATE OR REPLACE`: cùng
      câu lệnh chèn thông báo (CTE `RETURNING`) xếp một dòng gửi cho người nhận vừa được
      chèn thông báo và có `external_identities.provider = 'zalo'` (cột `zalo_id_for` đọc).
      `dw_app`: SELECT, UPDATE chỉ `status, attempts, next_attempt_at, external_message_id,
      last_error`; không INSERT, không DELETE (một cửa vào là grant, không phải quy ước).
      `platform.channel_delivery_scopes_due(channel)` và `platform.prune_channel_deliveries()`
      SECURITY DEFINER, EXECUTE cho `dw_app`, thu hồi khỏi PUBLIC.
      Adapter `dw_platform/adapters/persistence/channel_deliveries.py` (`SqlChannelOutbox`,
      `SqlChannelDeliveryRetention`); lane `dw_worker/consumers/channel_delivery.py`;
      `ChatRecipientUnreachableError` trong `dw_connectors.ports`, `ZaloBotClient` phân loại.
      `WorkerSettings.channel_delivery_interval_seconds` (30, 5..3600), `.env.example` bỏ `#`,
      compose truyền biến cho worker.
    - **Q1, phạm vi giao dịch: mỗi lần gửi một giao dịch, không mỗi tenant một giao dịch.**
      Lane hỏi các cặp (tenant, workspace) có dòng tới hạn, rồi mỗi dòng: một giao dịch gắn
      `app.tenant_id` + `app.workspace_id`, claim `FOR UPDATE SKIP LOCKED LIMIT 1`, gửi, ghi
      kết cục, commit. Lý do: bảng hẹp theo workspace nên cần cả workspace (không đặt
      `app.workspace_scope`, chỉ offboarding được đặt), và worker chết giữa chừng chỉ gửi lại
      đúng một tin, không cả lô (ADR 0013: ít nhất một lần). Tối đa 20 dòng mỗi scope mỗi
      tick. Vì thế hàm SECURITY DEFINER trả cặp id (tenant, workspace), không chỉ tenant.
    - **Q2, nội dung lấy từ đâu.** `title` và `link` được chép lên dòng gửi lúc xếp hàng
      (dấu đóng lúc tạo; `notifications` không bao giờ bị ứng dụng sửa nên không lệch).
      Không chép `body`: thân thông báo không bao giờ đi qua kênh. Tin =
      `[<tenant> · <workspace>]`, tiêu đề, `DW_PUBLIC_WEB_URL` + `link` (không link thì `/`).
      Đây là phần **QE-20 tạm thời**: chỉ định danh an toàn và tóm tắt ngắn (tiêu đề ≤ 200 ký
      tự) ra Zalo; không mã quyết định, không bí mật (ADR 0014) vì `compose` không nhận thân.
      Bên gửi thông báo phải giữ tiêu đề ở mức định danh; Elmich trả lời QE-20 thì xem lại.
    - **Q3, người nhận không liên kết.** Theo ticket và ADR 0013 (có liên kết là đồng ý):
      không có dòng nào. Liên kết gỡ trước lúc gửi: `cancelled`, `last_error =
      recipient_unlinked`, không gọi `send_message`. Thêm (an toàn hơn ticket): lúc gửi kiểm
      lại membership trong workspace và tenant `active`; không còn thì `cancelled`,
      `recipient_not_member`. Không có opt-in riêng mỗi người ngoài liên kết.
    - **Q4, lỗi vĩnh viễn hay tạm thời.** HTTP 400/403/404 hoặc `error_code` 400/403/404
      trong thân `ok:false` là `ChatRecipientUnreachableError` (vĩnh viễn: `failed` sau một
      lần). Còn lại, cả 401 (token của deployment, không phải chat của người), là tạm thời.
      **Tạm**: suy từ dialect Telegram, chưa đo với Zalo; ticket 07 xác nhận hoặc sửa.
    - **Q5, giãn cách.** 1, 2, 4, 8 phút (giờ của database); lần thứ 5 lỗi thì `failed`
      (`attempts_exhausted`). Audit một dòng cho mỗi kết cục (`channel_delivery.sent |
      failed | cancelled`, `resource_type = channel_delivery`, actor = người nhận, `details`
      có `actor = channel_delivery_lane`, `chat_id_hash`, `attempts`, lý do), cùng giao dịch;
      thử lại không ghi audit.
    - **Q6, dọn.** Lane riêng `channel_deliveries_retention` trên nhịp retention (mỗi pruner
      một lane, như `notifications_retention`), không gộp vào lane `retention`.
    - **Q7, khi nào lane chạy.** Có database và `ZALO_BOT_TOKEN`, bất kể poll hay webhook.
      Không token thì dòng vẫn được xếp (người chỉ liên kết được khi có bot) và nằm
      `pending`; dọn không xóa dòng `pending`, nên nếu bot bị gỡ hẳn thì dòng còn đó tới khi
      có token lại (ghi ở đây, chưa làm gì).
    - **Offboarding.** Xuất qua catalog như mọi bảng `tenant_isolation_%`; xóa bằng cascade
      khi purge xóa workspace (`dw_app` không có DELETE); test khẳng định không còn dòng nào.
    - **Test.** Integration dw_platform `test_channel_deliveries.py` (11): cửa vào, hai lần
      `deliver` một dòng, không liên kết không dòng, người ngoài workspace không dòng, `dw_app`
      không chèn/xóa/sửa ngoài trạng thái, grant theo catalog, RLS tenant khác và workspace
      khác không đọc không sửa, claim dưới scope lệch không thấy gì, hàm scopes, dọn,
      offboarding. Worker `test_channel_delivery_db.py` (10): gửi một lần không có thân,
      gỡ liên kết thì `cancelled`, tạm thời rồi thành công (2 lần, 1 `sent`), không tới được
      (1 lần, `failed`), trần (`failed`), hai worker song song không gửi trùng, `SKIP
      LOCKED` không chờ (timeout 3 giây), tenant khác mang đúng tên của nó, mất membership
      hoặc tenant bị khóa thì `cancelled`; mỗi trường hợp đúng một audit cuối. Unit: consumer
      (8), phân loại của `ZaloBotClient` (7), wiring worker (1 mới).
    - **Mutation** (mỗi cái đỏ, rồi trả lại): bỏ điều kiện liên kết ở cửa vào; bỏ cả
      `FOR UPDATE SKIP LOCKED` (hai worker gửi trùng); bỏ `status = 'pending'` lúc claim;
      chỉ bỏ `SKIP LOCKED` (test giữ dòng hết giờ chờ); dọn xóa cả `pending`; gửi khi đã gỡ
      liên kết; bỏ kiểm membership lúc gửi; lỗi vĩnh viễn bị thử lại; bỏ trần; bỏ link khỏi
      tin; bỏ phân loại 400/403/404 của Zalo; cấp INSERT cho `dw_app`.
    - **Ứng viên đưa ngược (không import `dw_supply_chain`):** migration `4a865a1c97aa`,
      `channel_deliveries.py`, `tables.channel_deliveries`, `consumers/channel_delivery.py`,
      wiring và settings của worker, `ChatRecipientUnreachableError` và phân loại trong
      `zalo_bot.py`, các test kèm theo.
