# 01 — Tin kênh chờ mãi khi bỏ bot token: quá hạn thì `failed` (`channel_unconfigured`)

Status: resolved (2026-10-08, nhánh `feat/security-debts`)
Blocked by: —
Area: platform-runtime

## Mục tiêu

`platform.deliver_notification` xếp hàng tin cho mọi người nhận đã liên kết, dù host có
gửi hay không (ADR 0006), và lượt prune không bao giờ xoá dòng `pending`. Bỏ bot token đi
thì không lane nào gửi, không gì đánh dấu hỏng: tin chờ mãi, người nhận không được gửi mà
cũng không ai biết là không gửi.

## Việc đã làm

- Policy retention `1.6.0` → `1.7.0`: `channel_deliveries.pending_expiry_days: 7`
  (`ChannelDeliveryRetention`, `ge=1`, bắt buộc: không có giá trị mặc định thứ hai trong
  code).
- Migration `983b509c3f0f`: function `platform.expire_pending_channel_deliveries`
  (interval, uuid, text), SECURITY DEFINER, EXECUTE thu khỏi PUBLIC và chỉ cấp `dw_app`; đánh `failed`,
  `last_error = 'channel_unconfigured'`, và ghi một dòng audit `channel_delivery.failed`
  mỗi dòng, trong tenant của nó, cùng câu lệnh.
- `SqlChannelDeliveryRetention(pending_expiry=...)`: prune trước, rồi expire, actor là
  `system:channel_deliveries_retention` (ADR 0011). Worker đọc hạn từ policy.
- Amendment ADR 0006.

## Quyết định tạm (Đạt giao)

- **Lane prune đánh hỏng, không chặn lúc xếp hàng:** việc xếp hàng nằm trong function DB
  của chính câu lệnh tạo notification, không biết host worker cấu hình gì, và host có thể
  được cấu hình sau.
- **7 ngày:** lane gửi xong một dòng trong vài giờ (5 lần, lùi từ 1 phút), nên 7 ngày dư
  xa mà vẫn không để người nhận chờ vô hạn.
- **Áp cho mọi dòng `pending` quá hạn**, không chỉ khi biết chắc kênh chưa cấu hình: dòng
  chờ lâu như vậy trên một host đang gửi cũng là dòng sẽ không được gửi.
- Dòng bị đánh hỏng được prune 90 ngày sau theo `created_at` cũ (tức ở lượt kế), dòng audit
  ở lại theo hạn của audit.

## Tiêu chí chấp nhận

- [x] Dòng pending quá hạn ở hai tenant → `failed`/`channel_unconfigured`, mỗi dòng một audit
      trong đúng tenant, actor là lane; dòng pending chưa quá hạn và dòng `sent` không đổi;
      lượt thứ hai không ghi thêm.
- [x] Chỉ `dw_app` được gọi function; PUBLIC không.
- [x] Mutation: function không đánh hỏng → 1 đỏ; bỏ audit → 1 đỏ; adapter không gọi expire
      → 1 đỏ (lần sed đầu không khớp nên không đột biến, làm lại thì đỏ); để PUBLIC gọi được
      → 1 đỏ; bỏ hạn → 1 đỏ.
