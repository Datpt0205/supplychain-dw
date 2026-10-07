# Bước 12–13: thiết kế màu và bao bì, test trước sản xuất (lát PK)

## Mục tiêu

Bước 12 (sơ đồ con thiết kế màu và bao bì) và phần test trước sản xuất của bước 13
thuộc Phần A, phạm vi chủ sở hữu đã chốt (17 bước). Lát này làm phần không chờ Phần B:
Cung ứng duyệt mẫu màu, Cung ứng duyệt thiết kế, nhận mẫu trước sản xuất, R&D test
trước sản xuất, và test đó là điều kiện để Hồ sơ PO vào `production`.

## Hiện trạng (kiểm ngày 5/10/2026)

- Bước 12 là một trạng thái thô `pre_production` của `POCase`; sơ đồ con 11 bước chưa
  có (`docs/products/elmich/process.md` mục 3.1, mục 4 hàng 12).
- Bước 13 chuyển `pre_production` → `production` bằng một hành động, không có kết quả
  test trước sản xuất.
- Chứng từ theo hồ sơ là lát D (`case-documents/issues/01`).

## Trong phạm vi

Ticket 01. MKT (lên nội dung bao bì, nhận báo màu) chưa là người dùng hệ thống tới Phần
B: lát này ghi việc "báo TP MKT" thành một dòng lịch sử và thông báo cho PIC.

## Ngoài phạm vi

- Phần của MKT và Thiết kế như người dùng (Phần B).
- Đóng cont thành bước riêng (bước 14, chờ QE-15; hoãn có tên ở QO-6).
- SLA từng bước con (chờ QE-03).

## Tiêu chí xong

Ticket 01 `resolved`; `process.md` mục 4 hàng 12–13 trỏ ticket này.

## Danh sách ticket

| #   | Ticket                                                                                       | Status   | Blocked by                                                                                                                               |
| --- | -------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 01  | [Duyệt màu, duyệt thiết kế, test trước SX](issues/01-colour-packaging-and-pre-production.md) | resolved | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md, .claude/plans/supply-chain/case-documents/issues/01-case-documents.md |
