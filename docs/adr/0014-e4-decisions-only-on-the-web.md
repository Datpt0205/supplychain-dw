---
status: Proposed
date: 2026-10-05
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide 96-155, channel="web" dòng 155
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # handle_update 73-99
---

# E4. Quyết định chỉ làm trên web; Zalo chỉ báo và dẫn liên kết

Tin Zalo báo "có việc cần duyệt" hoặc "quá hạn" kèm liên kết tới `/approvals` hay
trang hồ sơ. Người nhận mở liên kết, đăng nhập, xem đúng thứ họ duyệt và quyết trên
web. Lệnh duy nhất bot nhận là `/start <token>` và `/stop`, khớp nguyên văn. Mọi chữ
khác (kể cả "duyệt", "đồng ý", "hủy") không đổi trạng thái nào; bot trả nhiều nhất
một câu hướng dẫn. Bộ từ dừng hiện có trong `zalo_link.py:26` (`stop`, `/huy`,
`/hủy`, `hủy`, `huỷ`) bị thu về chỉ `/stop`: một người trả lời "hủy" dưới một tin báo
duyệt sẽ âm thầm gỡ liên kết Zalo của chính mình. Ticket Z1 làm việc này.

Lý do:

- Bot Platform không có nút; duyệt bằng chữ cần mô hình đọc câu rồi code quyết, tức
  một đường quyết thứ hai phải có test riêng.
- Danh tính trong chat là một chat id, không có tenant ([ADR 0012](0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)).
- Approval nghiêm (`strict_approval_prefixes`) đòi người khác người yêu cầu và một
  nhận xét bằng chữ; người duyệt phải thấy đúng phiên bản họ duyệt.
- `ApproveAndResumeService` tiếp tục run với `channel="web"`.

## Phương án đã cân nhắc

- **Duyệt bằng chữ như `DecisionEngine` của repo `dw` cũ.** Bác cho giai đoạn này:
  gắn chặt với sản phẩm đấu thầu cũ, và danh tính dựa vào bảng tên demo.

## Hệ quả

- Liên kết trong tin phải mở đúng mục sau đăng nhập, hoặc nói vì sao không mở được
  (`ui-quality.md` mục 11).
- Nếu Elmich cần duyệt trên điện thoại, câu trả lời là trang web dùng được ở 320 px,
  không phải duyệt trong chat.
