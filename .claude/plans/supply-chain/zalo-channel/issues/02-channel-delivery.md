# 02 — Hộp thư kênh `platform.channel_deliveries` và lane `channel_delivery`

Status: ready-for-agent
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

- [ ] **Test âm RLS:** tenant B không đọc, không sửa dòng của tenant A; workspace khác
      cùng tenant không đọc được; `app.workspace_scope = 'tenant'` chỉ mở trong lane
      offboarding. `test_rls_coverage.py` xanh với bảng mới.
- [ ] Hai lần `deliver` cùng `source_key` cho cùng người: một thông báo, một dòng gửi.
- [ ] Người nhận không liên kết: không có dòng gửi. Gỡ liên kết trước lúc gửi: dòng
      thành `cancelled`, không gọi `send_message`.
- [ ] Lỗi tạm thời rồi thành công: hai lần thử, một `sent`; lỗi vĩnh viễn: một lần thử,
      `failed`; quá trần: `failed`. Mỗi trường hợp có đúng một audit event cuối.
- [ ] Mutation: bỏ điều kiện "có liên kết" ở cửa vào thì một test đỏ; bỏ cả mệnh đề
      `FOR UPDATE SKIP LOCKED` (hoặc bỏ việc kiểm lại `status = 'pending'` khi claim)
      thì test hai worker song song gửi trùng và đỏ (ghi vào Comments). Chỉ bỏ
      `SKIP LOCKED` mà giữ `FOR UPDATE` thì không đỏ được: worker thứ hai chờ khóa, READ
      COMMITTED kiểm lại `status` sau khi khóa nhả và bỏ qua dòng.
- [ ] `SKIP LOCKED` có test riêng: khi worker thứ nhất đang giữ một dòng, worker thứ
      hai lấy dòng khác ngay, không bị chặn (đo bằng timeout ngắn).
- [ ] Dọn: dòng cũ hơn 90 ngày bị xóa trừ dòng `pending`; dòng trong hạn còn nguyên;
      mutation bỏ điều kiện `status <> 'pending'` thì test đỏ.
- [ ] `test_privileges.py` khẳng định grant; offboarding xuất và xóa bảng mới.
- [ ] `make ci` xanh.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-dw-channels.md` mục 1 (outbox `0011_dw01_slack_notifications.py:21-104`, consumer
  `slack_approvals.py:26-172` của repo `dw` cũ), gap 3, mục 3 (bảng dùng lại).
- `docs/products/elmich/surveys/2026-10-05-platform-auth-portal.md` mục 3 (`notifications.py:101-130`, `wiring.py:192`).
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 5 (gửi best-effort, không thử lại), 10 điểm 5.
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 2, "Data model" và "Outbound".

## Comments
