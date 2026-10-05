---
status: Proposed
date: 2026-10-05
source:
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/notifications.py # deliver, dòng 101-131
    - ../../packages/python/dw_platform/src/dw_platform/application/notifications.py # dòng 40-65
    - ../../db/migrations/versions/855ae928c3fa_platform_in_app_notifications.py
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/notifications.py # SqlNotificationRetention, dòng 133-145
    - ../../db/migrations/sql/0013_partition_maintenance.sql # ensure_time_partitions chỉ biết audit_events
    - ../../CLAUDE.md # Data model rules: partitions; Agent and tool rules: idempotency
---

# E3. Hộp thư gửi kênh trung lập nhà cung cấp: `platform.channel_deliveries`

Hôm nay thông báo chỉ tới hộp thư trong ứng dụng: `SqlNotificationRepository.deliver`
đi qua `platform.deliver_notification`, idempotent theo `source_key`. Không có đường
ra kênh nào. Gửi Zalo là một side effect, nên theo `CLAUDE.md` nó cần idempotency và
audit.

**Quyết định:** một bảng nền tảng `platform.channel_deliveries`, mỗi dòng là một lần
gửi một thông báo tới một người qua một kênh.

- Cột: `tenant_id`, `workspace_id`, `recipient_user_id`, `channel` (CHECK `'zalo'`),
  `source_key`, `status` (CHECK `pending | sent | failed | cancelled`), `attempts`,
  `next_attempt_at`, `external_message_id`, `last_error`, `created_at`, `updated_at`.
  UNIQUE `(tenant_id, channel, source_key, recipient_user_id)`.
- **Một cửa vào:** dòng được tạo trong cùng giao dịch với dòng thông báo, cho mỗi
  người nhận đang có liên kết kênh. Có liên kết là đồng ý nhận. Thông báo và lần gửi
  không thể lệch nhau vì không có đường tạo riêng.
- **Một lane worker `channel_delivery`:** lấy dòng tới hạn theo từng tenant, tra
  `zalo_id_for(user_id)` lúc gửi (liên kết đã gỡ thì `cancelled`), gửi qua
  `ChatSenderPort`, phân lỗi vĩnh viễn hay tạm thời, thử lại có giãn cách và trần số
  lần, ghi `external_message_id`, ghi audit mỗi lần gửi xong hoặc bỏ.
- **Đọc xuyên tenant như lane follow-up đã làm:** một hàm SECURITY DEFINER chỉ trả
  danh sách tenant có dòng tới hạn, rồi mỗi tenant một giao dịch có `app.tenant_id`.
- **Bảng thường, không partition,** như `platform.notifications` mà nó đi kèm. Dòng
  được sửa tại chỗ (`status`, `attempts`, `next_attempt_at`), nên bảng không phải
  append-only và luật partition của `CLAUDE.md` không áp. Partition cũng không làm
  được như viết lúc đầu: Postgres từ chối UNIQUE thiếu khóa partition;
  `platform._ensure_one_partition` (`0013_partition_maintenance.sql:31-33, 72-75`)
  ghi cứng policy chỉ theo tenant, nên partition tạo sau thiếu hình workspace; và
  `ensure_time_partitions`/`drop_expired_partitions` chỉ biết `audit_events`. RLS
  ENABLE + FORCE với hình policy workspace chuẩn của `CLAUDE.md`.
- **Có ngày xóa:** hàm `platform.prune_channel_deliveries()` (SECURITY DEFINER,
  không tham số) xóa dòng cũ hơn 90 ngày và không bao giờ xóa dòng `pending`; gọi
  từ lane `retention` sẵn có qua một `RetentionPrunePort` như
  `SqlNotificationRetention` (failure-modes #6).
- **Nội dung:** chữ thường, tên tenant và workspace, tiêu đề thông báo, liên kết
  tuyệt đối về web dựng từ `DW_PUBLIC_WEB_URL` và `link` tương đối của thông báo.
  Thân thông báo không được mang dữ liệu người nhận không được xem (`ui-quality.md`
  mục 6): kênh là một cửa phụ.

## Phương án đã cân nhắc

- **Gửi ngay sau commit, không lưu** (repo `sales_dw`). Bác: không có trạng thái,
  không thử lại, không audit.
- **Bảng `tender.approval_notification_jobs` của repo `dw` cũ.** Dùng lại thiết kế
  (claim, hủy khi cũ, lỗi vĩnh viễn hay tạm thời, `mark_sent`), không dùng lại bảng:
  cột mang tên Slack và revision id chọn tay.
- **Kênh do từng context tự gửi.** Bác: mỗi context sẽ có một bản gửi Zalo riêng.

## Hệ quả

- **Ít nhất một lần, không đúng một lần.** Zalo không nhận khóa idempotency. Worker
  chết sau khi gửi và trước khi ghi `sent` thì người nhận thấy tin hai lần. Chấp
  nhận; ghi ở đây để không ai coi đó là lỗi mới.
- Thêm kênh (email) là một giá trị CHECK và một adapter, không đổi bảng.
- Context không gửi Zalo trực tiếp; nó gửi thông báo, và thông báo đi ra kênh.
