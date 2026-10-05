---
status: Proposed
date: 2026-10-05
source:
    - ../../packages/python/dw_connectors/src/dw_connectors/ports.py # ChatSenderPort, dòng 27-41
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_bot.py # sau Z1: _split_for_zalo 31-56, ZaloBotClient 86-183 (trên main tại f0cd1a8: dòng 18-79)
    - ../../packages/python/dw_connectors/src/dw_connectors/adapters/zalo_link.py # sau Z1: token 51-98, parse_update 175-185, handle_update 195-238 (trên main tại f0cd1a8: token 25-47, handle_update 73-99)
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/zalo_link_repo.py # sau Z1: SqlZaloLink 108-266, audit _record 216-266 (trên main tại f0cd1a8: dòng 27-86)
    - ../../db/migrations/sql/0001_platform_grants.sql # dòng 63-73
    - ../../packages/python/dw_platform/src/dw_platform/application/identity.py # DbAccessContextFactory 62-101
    - ../../packages/python/dw_platform/src/dw_platform/adapters/persistence/membership_lookup.py # find_access 60-80
---

# E2. Zalo Bot Platform là kênh làm việc đầu tiên; liên kết nằm ở `external_identities`, token dùng một lần; lệnh đến dựng AccessContext từ membership của người đã liên kết, scope tối thiểu, chỉ ở server

Người dùng nội bộ của Elmich dùng Zalo, không dùng Slack. Kênh đầu tiên là **Zalo
Bot Platform** (`bot-api.zaloplatforms.com`), qua `ZaloBotClient` có sẵn trong
`dw_connectors`, thỏa `ChatSenderPort` (gửi một tin chữ). Người dùng tự liên kết:
bấm "Kết nối Zalo" ở trang cài đặt cá nhân, nhận dòng `/start <token>`, gửi cho bot.
Chat id Zalo được lưu ở `platform.external_identities` với `issuer = provider =
'zalo'` qua `SqlZaloLink`. `/stop` trong Zalo hoặc nút "Ngắt kết nối" gỡ liên kết.

**Một bot token cho mỗi deployment** (Đạt, 5/10/2026). Bot riêng cho từng tenant cần
một bảng liên kết có `tenant_id`; chưa làm.

## Những điều kiện đi kèm

1. **Liên kết không phải danh tính đăng nhập.** `external_identities` không có cột
   tenant và không có RLS. Đăng nhập an toàn chỉ vì `membership_lookup.py:77-80` so
   theo `issuer` của JWT đã kiểm, mà JWT không bao giờ mang `issuer='zalo'`. Ticket
   Z1 thêm test âm: một danh tính `zalo` không tìm ra membership nào qua đường đăng
   nhập. Webhook và lane poll không bao giờ gọi `access_context_factory` (đường của
   JWT).
2. **Lệnh đến dựng AccessContext theo một đường riêng, chỉ ở server** (sửa ngày
   5/10/2026, Đạt: Zalo là kênh làm việc hai chiều). Ngoài `/start` và `/stop`, một tin
   từ chat đã liên kết được xử lý như sau, và không bước nào đọc gì từ nội dung tin:
    - chat id được giải thành `user_id` qua `SqlZaloLink`; chat chưa liên kết chỉ nhận
      câu hướng dẫn liên kết;
    - tenant và workspace lấy từ server: với quyết định approval, từ dòng mã cấp trong
      phiên cổng ([ADR 0014](0014-e4-decisions-on-zalo-after-a-portal-view.md)); với đề
      xuất và câu hỏi, từ membership duy nhất của người đó, hoặc "workspace dùng cho
      Zalo" họ chọn trên trang cài đặt khi có nhiều membership; chưa chọn thì bot gửi
      liên kết tới trang cài đặt, không hỏi trong chat;
    - membership của `(user_id, tenant, workspace)` được tra theo `user_id`, không theo
      `issuer='zalo'`, nên đường đăng nhập vẫn không bao giờ nhận danh tính Zalo;
    - AccessContext mang scope tối thiểu: giao của scope membership với tập trần mà lệnh
      khai ở composition root (đề xuất, hỏi đáp chỉ đọc, quyết approval). Không trường
      nào rộng hơn membership. Trần lệch với quyền handler thật đòi thì lệnh bị từ chối,
      không mở rộng: hướng lệch đóng, và mỗi lệnh có test chạy được với đúng trần của nó.
    - tenant bị khóa hoặc membership đã gỡ thì từ chối như đăng nhập.
3. **Token dùng một lần.** Token HMAC hiện có hạn 15 phút nhưng dùng lại được trong
   hạn: ai thấy token đều gắn được Zalo của mình vào người đó. Token thêm `jti`; bảng
   `platform.channel_link_nonces` (jti khóa chính, `user_id` FK `ON DELETE CASCADE`
   có index, `expires_at`, `used_at`) đánh dấu đã dùng trong cùng giao dịch với lúc
   liên kết. Dòng hết hạn được dọn bởi lane worker.

    **Bị đổi trước** (mối đe dọa riêng, thêm 5/10/2026 sau review): nonce chặn lần dùng
    thứ hai, không chặn lần đầu. Người thấy mã `/start` qua vai và gửi trước chủ của nó
    sẽ gắn Zalo của mình vào tài khoản đó, và Z4, Z6 dựng quyền trên liên kết. Điều chặn:
    - mã chỉ hiện sau đăng nhập, sống 15 phút;
    - liên kết, liên kết lại và gỡ đều ghi một audit (`resource_type = channel_link`,
      kênh, băm chat id, người làm: bot hay web) và một thông báo trong app ở mọi tenant
      của người đó, trong cùng giao dịch với thay đổi; người bị chuyển mất chat cũng được
      báo (Z1 bước 9). Người không có membership không liên kết được;
    - `/settings` hiện "đã kết nối" và nút ngắt; Z4 có tiêu chí rằng một liên kết lạ hiện
      trong hộp thư và trên `/settings` trước mọi lệnh đề xuất;
    - quyết approval vẫn cần mã chỉ hiện trong phiên cổng của chủ
      ([ADR 0014](0014-e4-decisions-on-zalo-after-a-portal-view.md)), nên chat lạ không
      quyết được gì.

    Còn lại: giữa lúc liên kết lạ và lúc chủ đọc thông báo, chat lạ gửi được đề xuất (Z4)
    và hỏi chỉ đọc (Z6) với scope tối thiểu của lệnh. Audit cho biết chat nào và lúc nào.

4. **Chạy bằng `dw_app`, không bằng `dw_provisioner`.** `0001_platform_grants.sql:63-73`
   không cấp `external_identities` cho provisioner, trái với docstring
   `zalo_link_repo.py:6-8` (trên `main` tại f0cd1a8). Docstring được sửa theo grant,
   không ngược lại.
5. **Tên sản phẩm được tiêm.** `zalo_link.py:96` (trên `main` tại f0cd1a8) ghi cứng
   "Sale Intelligence" trong code nền tảng; câu trả lời của bot lấy tên từ cấu hình
   deployment.
6. **Cắt tin 1900 ký tự.** Zalo từ chối tin dài hơn 2000 ký tự mà không báo rõ (đo
   ở repo `dw` cũ, `zalo_bot.py:18-48`). Việc cắt là tính chất của kênh, nằm trong
   adapter.
7. **Token bot nằm trong URL.** Mọi lỗi httpx và log phải xóa token trước khi ghi
   (SEC-20 của sản phẩm đấu thầu).

## Phương án đã cân nhắc

- **Zalo OA cùng ZNS.** Gửi được tới người chưa nhắn trước, nhưng cần tài khoản doanh
  nghiệp xác minh, mẫu tin được duyệt và phí mỗi tin. Không cần cho nhân viên nội bộ.
- **Bản đồ Zalo id trong `.env` hoặc yaml** (repo `dw` cũ). Bác: chỉ dùng cho demo,
  và là bản sao thứ hai của danh sách người dùng.
- **Email trước.** Chưa có adapter và chưa có SMTP; để sau, cùng port.

## Hệ quả

- Bot Platform chỉ nhắn được người đã nhắn bot trước, nên nhà cung cấp không được
  nhắc qua kênh này. Elmich phải xác nhận kênh này chỉ cho nhân viên (QE-17).
- Một liên kết Zalo của một người dùng phục vụ mọi tenant họ là thành viên; tin gửi
  đi mang tên tenant và workspace để người nhận phân biệt.
- Không có nút bấm trong tin; tin gửi ra là chữ kèm liên kết về web. Tin đến là ba
  loại việc: đề xuất sản phẩm ở bước 1 (Z4), quyết approval sau khi xem trên cổng (Z5),
  hỏi chỉ đọc về hồ sơ được xem (Z6).
- Bản trước của ADR này nói liên kết không bao giờ dựng AccessContext (khảo sát
  `2026-10-05-zalo-sales-dw.md` mục 10 điểm 1). Điều còn giữ: liên kết không bao giờ là
  danh tính đăng nhập, và không gì trong tin chọn tenant, workspace, người hay quyền.
