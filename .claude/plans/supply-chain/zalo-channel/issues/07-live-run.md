# 07 — Chạy thật kênh Zalo với một bot và một tài khoản Zalo thật

Status: ready-for-human
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md, .claude/plans/supply-chain/zalo-channel/issues/02-channel-delivery.md, .claude/plans/supply-chain/zalo-channel/issues/03-zalo-webhook.md, .claude/plans/supply-chain/zalo-channel/issues/04-chat-proposal.md, .claude/plans/supply-chain/zalo-channel/issues/05-approve-via-zalo.md, .claude/plans/supply-chain/zalo-channel/issues/06-read-only-qa.md, .claude/plans/supply-chain/hosting/issues/02-live-domain.md
Area: supply-chain

## Mục tiêu

Các tiêu chí cần một người, một điện thoại có Zalo, một bot thử và (cho webhook) một
tên miền. Tách khỏi Z1–Z6 để các ticket agent đóng được trung thực, và để không ô nào
được tick khi chưa chạy (failure-modes #1, trường hợp "trivy wired").

## Việc cần làm

1. Ở máy cá nhân (`make infra-up`, `make dev`, `ZALO_UPDATES_MODE=poll`): liên kết một
   Zalo thật qua trang cài đặt và `/start <token>`; gửi lại cùng token bị từ chối;
   `/stop` gỡ liên kết. (Z1)
2. Một follow-up của Hồ sơ PO tới Zalo của người đã liên kết, đúng một lần, liên kết
   trong tin mở đúng trang sau đăng nhập. (Z2)
3. Trên tên miền của `hosting/issues/02`: `scripts/zalo_webhook.py set`, `info`,
   `delete` với bot thử; chuyển `ZALO_UPDATES_MODE=webhook`, lặp bước 1 qua webhook. (Z3)
4. Gửi bot một đề xuất có dấu, một không dấu, kèm một ảnh; trả lời trường thiếu; "Đồng ý"
   tạo đúng một hồ sơ có ảnh. Ghi hình dạng update ảnh thật nếu khác fixture của Z4. (Z4)
5. Một approval bước 6: tin Zalo có tóm tắt và liên kết, không có mã; mở trên điện thoại,
   lấy mã, `DUYỆT <mã> <nhận xét>` được nhận; gửi lại cùng lệnh bị từ chối; xem, sửa hồ
   sơ, gõ mã bị từ chối vì phiên bản; một tài khoản Zalo thứ hai gõ mã của người thứ nhất
   bị từ chối. (Z5)
6. Hai câu hỏi về hồ sơ được xem và một câu về hồ sơ của workspace khác. (Z6)

## Tiêu chí chấp nhận

- [ ] Sáu bước trên chạy, mỗi bước ghi lệnh, thời điểm và kết quả vào Comments.
- [ ] Mọi sai khác với thiết kế (giới hạn tốc độ, độ dài tin, mã lỗi Zalo) ghi vào
      Comments và thành ticket nếu cần sửa.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 8 (hành vi Zalo đã gặp),
  mục 9 (chạy cục bộ và host công khai).

## Comments

- 2026-10-07, từ Z2: đo và ghi ở đây mã lỗi thật của Zalo `sendMessage` khi chat không tồn
  tại và khi người dùng chặn bot. Z2 tạm coi HTTP hoặc `error_code` 400/403/404 là vĩnh viễn
  (`_UNREACHABLE` trong `zalo_bot.py`, suy từ dialect Telegram); sai thì sửa ở đó.
