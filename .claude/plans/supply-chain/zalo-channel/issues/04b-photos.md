# 04b — Ảnh sản phẩm gửi qua Zalo khi đề xuất (bước 8 cũ của Z4)

Status: ready-for-human
Blocked by: .claude/plans/supply-chain/zalo-channel/issues/04-chat-proposal.md
Area: supply-chain

## Mục tiêu

Ảnh một PIC gửi bot trong lúc đề xuất sản phẩm thành `product_image` của đúng hồ sơ
được tạo. Tách khỏi Z4 (quyết định của lead, 7/10/2026) vì hình dạng update ảnh của
Bot Platform chưa ai đo (failure-modes #4): `parse_update` chỉ đọc chữ, và không có
fixture thật nào cho ảnh. Trong Z4, một update ảnh nhận câu trả lời cố định ("Mình chưa
nhận ảnh qua Zalo; anh/chị tải ảnh ở trang hồ sơ sau khi tạo") và không gì được lưu
hay tải.

## Việc của người (trước khi agent làm)

1. Đạt gửi bot thử một ảnh JPEG từ một Zalo đã liên kết (lane poll ở máy cá nhân).
2. Lưu update thô đúng như `getUpdates` trả, **xóa bot token** khỏi mọi URL trong đó
   (và chat id thật nếu muốn), commit làm fixture dưới
   `packages/python/dw_connectors/tests/fixtures/zalo/`.
3. Đổi Status thành `ready-for-agent`.

## Việc cần làm (sau khi có fixture)

1. Đọc ảnh từ fixture: trường nào chứa URL hay id, host nào; không viết code ảnh theo
   tài liệu hay đoán.
2. Tải ảnh chỉ từ host của Zalo qua https (danh sách host lấy từ fixture), trần kích
   thước của lát D, chỉ JPEG, PNG theo byte đầu; lưu tạm dưới
   `supply_chain/{tenant}/{workspace}/staging/`; gắn vào hồ sơ thành `product_image`
   qua `UploadCaseDocument` khi tạo; ảnh của bản nháp bỏ dở được lượt quét mồ côi của
   lát D xóa.
3. Ảnh đến sau tóm tắt làm đổi `draft_version`, nên "Đồng ý" bị đòi tóm tắt lại (Z4b).
4. Trần của lệnh đề xuất thêm `supply_chain.document.write` khi và chỉ khi ticket này
   làm xong (ADR 0012 amendment của Z4b).

## Tiêu chí chấp nhận

- [ ] **Ảnh** (chuyển từ ticket 04): host ngoài danh sách bị từ chối không tải; tệp
      không phải JPEG, PNG bị từ chối; ảnh nhận được thành `product_image` của đúng hồ
      sơ, `object_key` dưới tiền tố tenant và workspace.
- [ ] Fixture thật đã commit, không chứa bot token.
- [ ] Mỗi lớp chặn (host, loại tệp, cỡ) có test đỏ khi gỡ nó.

## Nguồn

- `.claude/plans/supply-chain/zalo-channel/issues/04-chat-proposal.md` bước 8 cũ.
- `docs/products/elmich/surveys/2026-10-05-zalo-sales-dw.md` mục 8 điểm 7 (cỡ tin chưa
  đo).

## Comments

- 2026-10-07: tạo theo quyết định Z4 của lead (tách ảnh khỏi Z4). Chưa có fixture.
