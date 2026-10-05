# Brief bộ slide PoC Elmich (2026-10-05)

Brief cho Claude Design dựng lại bộ slide PoC gửi Elmich. Code của repo này phải khớp
với những gì slide hứa; chỗ nào code chưa có thì slide ghi đúng là "đang làm".

Quy cách: font Helvetica, chữ thường 11pt; tiêu đề là một câu kết luận; mỗi slide một ý;
mẫu của Proterial; ví dụ tin Zalo vẽ như khung chat, ghi "Ví dụ minh họa".

## Thay đổi so với bản đầu

- Phạm vi PoC là bước 1–10. Bước 1 là nơi gán PIC (Person In Charge, người phụ trách:
  người tạo sản phẩm ở bước 1, theo hồ sơ suốt các bước sau).
- Zalo là kênh làm việc hai chiều: PIC chat đề xuất sản phẩm, quản lý duyệt trên Zalo
  sau khi đã xem hồ sơ trên portal, mọi người hỏi đáp về hồ sơ.
- Tuần 1 dựng portal, tuần 2 bật Zalo và chạy thử trên hồ sơ thật.
- Bước 11–17 ghi "đã có trong DW, nối liền ở đợt 2".
- Kết nối Zalo Bot Platform đã chạy ở dự án DW khác; giới hạn còn lại: bot chỉ nhắn được
  người đã nhắn bot trước, nên chỉ dùng cho nhân viên, không nhắn nhà cung cấp.

## Từng slide

1. **Phạm vi PoC.** PoC 2 tuần đưa bước 1–10 lên Digital Worker, từ đề xuất sản phẩm tới
   tạo PO. Băng 10 bước, dưới mỗi bước là việc DW làm; thêm "Tạo hồ sơ, gán PIC" ở bước 1.
   Ngoài phạm vi: bước 11–17, Phần B, ERP, nhắn nhà cung cấp qua Zalo.
2. **Bước 1 và quy tắc PIC.** Người tạo đề xuất là PIC; đổi PIC có lý do và lịch sử; đề
   xuất qua web hoặc chat Zalo; PIC nhận mọi nhắc hạn, quá hạn kéo dài báo TP Cung ứng.
3. **Hai kênh làm việc.** Web cho việc cần màn hình đầy đủ (hồ sơ, bản nháp, tài liệu,
   báo cáo); Zalo cho việc nhanh (đề xuất, cập nhật, nhắc hạn, duyệt, hỏi đáp). Cùng một
   hồ sơ, cùng lịch sử.
4. **Tuần 1: portal.** Login theo 5 vai (Cung ứng, R&D, TP Cung ứng, BGĐ, Kế toán); hồ sơ
   bước 1–10 với SLA và vòng chỉnh sửa mẫu; bản nháp phiếu chỉnh sửa, tờ trình, BM04,
   email chốt NCC, PO nháp; kiểm trùng mã hàng/SKU; nút ĐẶT HÀNG; trang Cài đặt để liên
   kết Zalo. Demo N5.
5. **Tuần 2: Zalo.** N6 liên kết Zalo; N6–N7 nhắc hạn, báo việc, báo cáo test mẫu cuối
   ngày; N7–N8 chat đề xuất, duyệt, hỏi đáp; N6–N9 chạy thử; N10 nghiệm thu.
6. **Chat trên Zalo.** Ví dụ đề xuất nhiều lượt (bot hỏi lại trường thiếu, tóm tắt, chờ
   "Đồng ý" mới tạo hồ sơ) và ví dụ hỏi tình trạng mẫu. AI chỉ đọc hiểu; hệ thống kiểm
   dữ liệu; không hiểu thì báo chưa hiểu, không đoán.
7. **Duyệt trên Zalo sau khi xem trên portal.** Tiêu đề: quản lý duyệt trên Zalo sau khi
   đã xem hồ sơ trên portal; hệ thống không nhận quyết định của người chưa xem. Luồng: tin
   Zalo có tóm tắt và liên kết (không có mã) → người duyệt mở portal, xem đủ hồ sơ, trang
   hiện mã dùng một lần → trả lời "DUYỆT 4821" hoặc "TỪ CHỐI 4821 + lý do" trên Zalo →
   bot xác nhận và hồ sơ chuyển bước. Bốn chốt: chỉ người có vai duyệt; phải xem đúng
   phiên bản trên portal mới có mã; mã dùng một lần, hồ sơ đổi thì mã hết hiệu lực; người
   trình không tự duyệt, mọi quyết định ghi lịch sử (kênh, phiên bản, lúc xem). Quyết định
   ở ADR 0014; loại duyệt nghiêm có cần nhận xét hay không còn chờ chốt (QO-7).
8. **Bản nháp chứng từ.** Bảng bước 4, 7, 8, 9, 10; BM04 đánh dấu ô thiếu hoặc mâu thuẫn,
   không tự điền đoán; kiểm trùng mã hàng/SKU bằng đối chiếu danh mục.
9. **Báo cáo và nhắc hạn.** Bản tin cuối ngày cho TP Cung ứng, mỗi con số dẫn về hồ sơ;
   nhắc PIC khi sắp tới hạn, báo TP khi quá hạn kéo dài; giờ gửi do Elmich chọn.
10. **Kế hoạch 10 ngày.** Khảo sát N1–N2; môi trường N1–N3; portal N2–N5; liên kết Zalo
    và nhắc hạn N6–N7; chat, duyệt, hỏi đáp N7–N8; chạy thử N6–N9; nghiệm thu N10. Mốc:
    chốt phạm vi N2, demo portal N5, demo Zalo N8, nghiệm thu N10.
11. **Hạ tầng.** Phương án A cloud (webhook trên HTTPS), phương án B máy chủ Elmich (bot
    tự hỏi tin định kỳ, vẫn chat hai chiều, không mở cổng từ Internet vào).
12. **Elmich chuẩn bị gì.** Đầu mối, biểu mẫu, 19 câu hỏi quy trình; thêm: thang ưu tiên
    ở bước 1, việc nào duyệt trên Zalo, ai được đổi PIC.
13. **Bàn giao và bước tiếp theo.** Hệ thống chạy trên hồ sơ thật, báo cáo đo kết quả
    (thêm tỷ lệ việc làm qua Zalo), lộ trình đợt 1 bước 1–10 dùng thật, đợt 2 bước 11–17.

## Cái gì đã có code, cái gì đang làm (để slide không hứa quá)

- Đã có: theo dõi đơn hàng bước 10–17; login theo user; trang Cài đặt và liên kết Zalo.
- Đang làm: bước 1–9 (ticket stage-1), chat đề xuất, duyệt và hỏi đáp qua Zalo (Z4–Z6),
  gửi tin qua kênh (Z2). Nếu phải thu hẹp trong 2 tuần, cắt hỏi đáp trước.
