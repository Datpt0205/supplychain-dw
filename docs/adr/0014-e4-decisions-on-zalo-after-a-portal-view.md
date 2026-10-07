---
status: Accepted
date: 2026-10-05
source:
    - ../../packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py # decide 96-155, _enforce_strict_rules 46-61, channel="web" dòng 155
    - ../../packages/python/dw_platform/src/dw_platform/domain/approval.py # ApprovalRequest 44-58, version dòng 58
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # sau Z1: handle_update 195-238, chỉ /start và /stop (trên main tại f0cd1a8: _STOP_WORDS dòng 26, handle_update 73-99)
    - ../products/elmich/surveys/2026-10-05-dw-channels.md # mục 1 DecisionEngine, mục 3 "concept only"
    - ../products/elmich/surveys/2026-10-05-zalo-sales-dw.md # mục 8 điểm 5 (không có nút)
---

# E4. Quyết approval trên Zalo được, nhưng chỉ sau khi đã xem đúng phiên bản trên cổng, bằng một mã dùng một lần

Bản trước của ADR này (cùng ngày) để mọi quyết định trên web, Zalo chỉ báo và dẫn
liên kết. Đạt sửa ngày 5/10/2026: Zalo là kênh làm việc hai chiều, và người duyệt
được trả lời quyết định trong Zalo, với điều kiện đã mở hồ sơ trên cổng trước. Quyết
trên web vẫn giữ nguyên.

## Quyết định

1. **Xem trên cổng sinh biên nhận và mã.** Người duyệt mở approval trên cổng (trang
   approval hoặc trang hồ sơ). Lần mở đó ghi một **biên nhận đã xem** (người, approval,
   phiên bản approval, phiên bản hồ sơ) và trang hiện một **mã dùng một lần** 4 chữ số
   cùng câu lệnh mẫu: `DUYỆT 4821` hoặc `TỪ CHỐI 4821 <lý do>`. Mỗi lần mở lại sinh biên
   nhận mới và mã mới; mã cũ của cùng người cho cùng approval bị thu hồi.
2. **Tin Zalo không bao giờ mang mã.** Tin báo "có việc cần duyệt" mang tóm tắt và liên
   kết tới trang approval. Mã chỉ hiện trên trang, sau đăng nhập.
3. **Server nhận một quyết định từ Zalo khi và chỉ khi tất cả đều đúng:**
    - chat id Zalo khớp một liên kết, và người được liên kết là người mã được cấp cho;
    - mã đúng approval, chưa dùng, chưa thu hồi, chưa hết hạn (15 phút);
    - người đó đã xem đúng phiên bản hiện tại: phiên bản approval và phiên bản hồ sơ trên
      biên nhận bằng phiên bản lúc quyết;
    - người đó giữ `approvals.decide` và `required_scope` đóng dấu trên approval
      ([ADR 0020](0020-e10-approval-decider-stamped-as-required-scope.md)), tính lại lúc
      quyết từ membership hiện tại;
    - người đó không phải người yêu cầu, với **mọi** loại approval (trên web, luật này chỉ
      áp cho tiền tố nghiêm).
4. **Thay đổi hồ sơ sau lúc xem làm mã mất hiệu lực.** Phiên bản hồ sơ đọc qua một port
   do nền tảng khai báo và context sở hữu hồ sơ thỏa, đăng ký theo tiền tố
   `approval_type` ở composition root. Loại approval chưa có ai trả phiên bản thì cổng
   không cấp mã và nói "Loại yêu cầu này chỉ quyết trên web" (đóng khi thiếu).
5. **Cùng một đường quyết.** Quyết định từ Zalo đi qua `ApproveAndResumeService.decide`,
   như web, với `channel="zalo"`; mọi kiểm có sẵn (quyền, `required_scope`, luật tiền tố
   nghiêm kể cả nhận xét bắt buộc) chạy một lần ở một chỗ. Mã được tiêu bằng một câu
   `UPDATE ... WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > now()` trong
   cùng giao dịch ghi quyết định; quyết định lỗi thì mã không bị tiêu.
6. **Lệnh là văn phạm cố định, không qua mô hình.** `DUYỆT <mã> [nhận xét]` và
   `TỪ CHỐI <mã> <lý do>`, chuẩn hóa chữ hoa thường và dấu. Mô hình không bao giờ ở trên
   đường quyết: không công cụ quyết, không trường "quyết định" trong schema ý định nào.
   Từ chối luôn đòi lý do.
   **Chưa quyết, chờ Đạt (QO-7):** nhận xét cho loại approval nghiêm. Dạng Đạt đưa là
   `DUYỆT 4821` trần, còn `decide` đòi nhận xét bằng chữ cho tiền tố nghiêm
   (`_enforce_strict_rules`). Đề xuất trong bản này: loại nghiêm đòi
   `DUYỆT 4821 <nhận xét>`, `DUYỆT 4821` trần bị từ chối với câu hướng dẫn, và trang hiện
   đúng dạng lệnh cần gõ. Hai phương án khác: (a) trang hỏi trước một nhận xét mặc định
   khi cấp mã và `DUYỆT 4821` trần dùng nhận xét đó; (b) loại nghiêm chỉ quyết trên web,
   cổng không cấp mã cho chúng. Z5 không bắt đầu phần lệnh `DUYỆT` cho loại nghiêm trước
   khi QO-7 được trả lời.
7. **Mọi kiểm hỏng đều từ chối, và bot nói vì sao** bằng một câu tiếng Việt. Mã sai và mã
   của người khác nhận cùng một câu, để không lộ approval của ai. Sai mã 5 lần trong 15
   phút thì mọi mã đang mở của người đó bị thu hồi.
8. **Audit:** mỗi quyết định ghi `channel` (`web` hoặc `zalo`), phiên bản approval, phiên
   bản hồ sơ, id biên nhận, id mã và id tin Zalo; mỗi lần từ chối ghi một audit event với
   mã lý do. Dòng quyết định (`platform.approval_decisions`) thêm cột `channel`.
9. **Chỉ một lệnh gỡ liên kết.** Bộ từ dừng (`zalo_link.py:26` trên `main` tại f0cd1a8:
   `stop`, `/huy`, `/hủy`, `hủy`, `huỷ`) thu về chỉ `/stop` (ticket Z1, đã bỏ
   `_STOP_WORDS`; `handle_update` khớp nguyên văn `/stop`); khi người dùng gõ quyết định
   trong chat, "hủy" càng dễ xuất hiện và không được gỡ liên kết của họ.

Vé: `zalo-channel/issues/05` (Z5).

## Lý do (giữ từ bản trước, nay là lý do của từng kiểm)

- Bot Platform không có nút; duyệt bằng chữ là một đường quyết thứ hai và phải có test
  riêng ở mọi lối từ chối. Vì thế lệnh là văn phạm cố định, và mọi lối từ chối có test âm.
- Danh tính trong chat là một chat id, không có tenant
  ([ADR 0012](0012-e2-zalo-bot-platform-is-the-first-outbound-channel.md)). Vì thế tenant
  và workspace lấy từ dòng mã (cấp trong một phiên cổng đã xác thực), không từ tin.
- Approval nghiêm đòi người khác người yêu cầu và một nhận xét bằng chữ; người duyệt phải
  thấy đúng phiên bản họ duyệt. Vì thế biên nhận mang phiên bản, và chat không làm yếu
  sàn của nền tảng.
- `ApproveAndResumeService` tiếp tục run với `channel="web"` cứng
  (`approval_flow.py:155`). Vì thế `decide` nhận kênh làm tham số, và run biết quyết định
  đến từ đâu.

## Các trường hợp đe dọa

| Trường hợp                                                   | Điều chặn                                                                                                                                                                                                                                                     |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mất điện thoại đang mở Zalo                                  | Không có phiên cổng thì không thấy mã; đoán mã cần một mã đang mở (người dùng vừa xem trong 15 phút) và bị khóa sau 5 lần sai. Còn lại: điện thoại có cả phiên cổng đang mở thì như mất máy tính đang đăng nhập; người dùng gỡ liên kết từ cổng hoặc `/stop`. |
| Tin bị chuyển tiếp                                           | Tin không mang mã. Một mã bị chép sang tài khoản Zalo khác thuộc về người khác, hoặc chat không liên kết: không khớp, từ chối, và lần sai tính cho người gửi, không cho nạn nhân.                                                                             |
| Phát lại (gửi lại cùng lệnh, Zalo giao trùng một update)     | Mã tiêu một lần bằng câu điều kiện trong giao dịch quyết; approval chỉ quyết được một lần (`_require_pending`); tin Zalo khử trùng theo id tin (Z4).                                                                                                          |
| Phiên bản cũ (hồ sơ đổi sau khi xem)                         | Phiên bản hồ sơ và approval trên biên nhận khác phiên bản lúc quyết: từ chối, bot bảo mở lại liên kết để xem bản mới và lấy mã mới.                                                                                                                           |
| Người yêu cầu tự duyệt                                       | Cổng không cấp mã cho người yêu cầu; dịch vụ quyết từ Zalo từ chối người yêu cầu với mọi loại approval; `decide` từ chối với tiền tố nghiêm. Ba lớp, mỗi lớp một test.                                                                                        |
| Câu chữ tự do hoặc prompt injection ("duyệt hết", "approve") | Không khớp văn phạm thì không phải quyết định; mô hình không có đường tới `decide`.                                                                                                                                                                           |
| Mất quyền sau khi xem                                        | Quyền tính lại lúc quyết từ membership hiện tại, không từ lúc xem.                                                                                                                                                                                            |

## Phương án đã cân nhắc

- **Chỉ quyết trên web** (bản trước). Bác (Đạt, 5/10/2026): người duyệt hay ở ngoài, và
  luồng duyệt của Elmich chạy trên Zalo.
- **Duyệt bằng chữ không cần xem trước, như `DecisionEngine` của repo `dw` cũ.** Bác: quyết
  mà không chứng minh đã thấy hồ sơ; danh tính dựa vào bảng tên demo; gắn chặt sản phẩm đấu
  thầu cũ.
- **Mã nằm trong tin Zalo.** Bác: mã khi đó chỉ chứng minh cầm điện thoại, không chứng minh
  đã xem; tin chuyển tiếp mang theo mã.
- **Mô hình đọc câu trả lời thành quyết định.** Bác: được yêu cầu chọn thì mô hình sẽ chọn,
  kể cả khi câu không nói gì (CLAUDE.md, "Understanding vs Deciding").
- **Nhận xét cho loại nghiêm** (QO-7, chưa quyết): đòi `DUYỆT <mã> <nhận xét>` (đề xuất ở
  điểm 6), nhận xét mặc định nhập trên trang khi cấp mã, hoặc loại nghiêm chỉ quyết trên
  web. Hai cách sau giữ đúng dạng `DUYỆT 4821` Đạt đưa; cách đầu không cần thêm bước trên
  trang.
- **Mã 6 chữ số.** Chưa chọn: Đạt đưa ví dụ 4 chữ số; giới hạn 5 lần sai mỗi 15 phút giữ
  xác suất đoán trúng dưới 5 trên 10 000 cho mỗi mã đang mở. Đổi độ dài là một thay đổi
  một dòng nếu cần.

## Hệ quả

- Hai bảng mới có RLS: `platform.approval_view_receipts`,
  `platform.approval_decision_codes` (mã lưu HMAC-SHA256 với khóa của server: băm trần một
  mã 4 chữ số đảo được bằng 10 000 lần thử, nên không phải bảo vệ).
- Liên kết trong tin phải mở đúng mục sau đăng nhập, hoặc nói vì sao không mở được
  (`ui-quality.md` mục 11); trang approval phải dùng được ở 320 px, vì người duyệt mở nó
  trên điện thoại để lấy mã.
- Tóm tắt trong tin và câu trả lời của bot đi qua máy chủ Zalo. Elmich xác nhận dữ liệu
  nào được đi qua (QE-20).

## Sửa đổi 2026-10-06 (Đạt giao quyết, QO-7)

- **Nhận xét viết trên cổng khi cấp mã.** Người duyệt phải mở cổng để thấy mã, nên trang
  cấp mã có ô nhận xét (bắt buộc với loại nghiêm); mã gắn với nhận xét đó. Tin Zalo chỉ
  mang `DUYỆT <mã>` hoặc `KHÔNG <mã> <lý do>`; quyết định ghi đúng nhận xét đã nhập trên
  cổng (với `KHÔNG`, lý do trong tin được nối sau nhận xét).
- **Mã 6 chữ số, hiệu lực 10 phút, dùng một lần; sai 5 lần thì khóa mã đó.** Thay 4 chữ
  số và 15 phút ở trên; băm HMAC giữ nguyên.

## Sửa đổi 2026-10-07 (Z5, lead theo ủy quyền của Đạt)

Trạng thái: Accepted (tạm; xem lại khi chạy thật ở ticket 07).

- **Cấp mã là một lần xem có nhận xét.** `POST /approvals/{id}/view` ghi biên nhận mỗi lần
  mở; với `issue_code` ghi biên nhận mới và mã mới gắn nhận xét, thu hồi mã mở cũ cùng giao
  dịch. Loại nghiêm cần nhận xét mới có mã.
- **Văn phạm.** `DUYỆT <6 số>` đúng nguyên tin; `KHÔNG <6 số> <lý do>`; "không" chỉ là lệnh khi
  theo sau là số. `TỪ CHỐI` thôi dùng.
- **Sai 5 lần khóa mã:** mỗi lần sai tính cho mọi mã đang mở của người gửi (tin không nêu
  approval), bộ đếm trên dòng mã; không có bảng đếm riêng. Lần sai không khớp mã nào chỉ log.
- **Một câu cho mọi mã không khớp;** trạng thái mã của chính người gửi (hết hạn, đã dùng, đã
  thay, bị khóa) mỗi trạng thái một câu.
- **`decide(channel=, admission=)`.** `DecisionAdmission` theo từng quyết định (khác
  `DecisionGuard` theo loại), sau mọi kiểm, trước mọi ghi, cùng giao dịch; quyết định được
  admit ghi audit `approval.channel_decided` cùng giao dịch.
- **Worker quyết** bằng `ApproveAndResumeService` của mình trên runner chứa graph của loại đó;
  loại không có port phiên bản không bao giờ quyết qua chat.
- **Khóa** `DW_APPROVAL_CODE_SECRET` (≥ 16 byte); trống thì không cấp mã và bot trả "chưa bật".
- **Liên kết** `/approvals/<id>?workspace=<ws>` (`approval_link`); trang đổi workspace nếu là
  thành viên, không thì "không tìm thấy".
