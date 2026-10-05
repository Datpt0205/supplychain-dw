# 04 — Chạy thật kênh Zalo với một bot và một tài khoản Zalo thật

Status: ready-for-human
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/01-zalo-link.md, .claude/plans/supply-chain/zalo-channel/issues/02-channel-delivery.md, .claude/plans/supply-chain/zalo-channel/issues/03-zalo-webhook.md, .claude/plans/supply-chain/hosting/issues/02-live-domain.md
Area: supply-chain

## Mục tiêu

Các tiêu chí cần một người, một điện thoại có Zalo, một bot thử và (cho webhook) một
tên miền. Tách khỏi Z1–Z3 để các ticket agent đóng được trung thực, và để không ô nào
được tick khi chưa chạy (failure-modes #1, trường hợp "trivy wired").

## Việc cần làm

1. Ở máy cá nhân (`make infra-up`, `make dev`, `ZALO_UPDATES_MODE=poll`): liên kết một
   Zalo thật qua trang cài đặt và `/start <token>`; gửi lại cùng token bị từ chối;
   `/stop` gỡ liên kết. (Z1)
2. Một follow-up của Hồ sơ PO tới Zalo của người đã liên kết, đúng một lần, liên kết
   trong tin mở đúng trang sau đăng nhập. (Z2)
3. Trên tên miền của `hosting/issues/02`: `scripts/zalo_webhook.py set`, `info`,
   `delete` với bot thử; chuyển `ZALO_UPDATES_MODE=webhook`, lặp bước 1 qua webhook. (Z3)

## Tiêu chí chấp nhận

- [ ] Ba bước trên chạy, mỗi bước ghi lệnh, thời điểm và kết quả vào Comments.
- [ ] Mọi sai khác với thiết kế (giới hạn tốc độ, độ dài tin, mã lỗi Zalo) ghi vào
      Comments và thành ticket nếu cần sửa.

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 8 (hành vi Zalo đã gặp),
  mục 9 (chạy cục bộ và host công khai).

## Comments
