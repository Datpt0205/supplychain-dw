# Hướng dẫn sử dụng Digital Worker chuỗi cung ứng (theo vai)

Bản 2026-10-10, ticket UAT-1. Dành cho người dùng Elmich. Ảnh màn hình sẽ được chèn khi
môi trường UAT chạy (chụp tự động); tới đó mỗi mục ghi tên trang và nút đúng như trên
màn hình.

## Điều chung cho mọi vai

- **Đăng nhập** bằng tài khoản công ty. Thanh menu trên cùng chỉ hiện trang bạn có quyền
  xem. Nút bị khóa luôn kèm lý do (ví dụ "Bước này cần nhiệm vụ R&D; vai của bạn chưa
  có").
- **AI chuẩn bị, bạn quyết.** Ở mỗi bước, AI soạn chứng từ (mục **Bản nháp**), đọc file NCC
  bạn tải lên (mục **Chứng từ**), so với hồ sơ và trình "chuyển sang bước X với chứng từ
  Y". Bước chỉ đổi khi người có nhiệm vụ bấm duyệt. Ô kết quả (Đạt / Không đạt, số đếm,
  số tiền đã trả) luôn để trống; gợi ý của hệ thống nằm bên cạnh, không chọn sẵn.
- **Đọc nhãn của AI.** Câu AI viết có nhãn "AI viết, đã kiểm dẫn chứng" và nguồn; câu
  nào có số không có trong nguồn đã bị bỏ trước khi bạn thấy. Giá trị AI đọc từ file hiện
  kèm trích dẫn của file.
- **Ảnh và PDF quét** chưa đọc được (trang ghi "máy không đọc được"): bạn kiểm bằng mắt.
  Ưu tiên PDF có lớp chữ, Word, Excel hoặc email.
- **Hỏi về hồ sơ.** Trên trang mỗi hồ sơ có thẻ **Hỏi về hồ sơ**: gõ câu hỏi (ví dụ "BM04
  ghi MOQ bao nhiêu?"), AI trả lời chỉ từ hồ sơ đó, mỗi câu kèm nguồn; không đủ bằng
  chứng thì nói vậy. Giá chỉ hiện với người có quyền xem giá.
- **Kết nối Zalo** (một lần): **Cài đặt** → thẻ **Zalo** → lấy lệnh `/start <mã>` (**Chép
  lệnh** hoặc **Mở Zalo**) và gửi cho bot của công ty; mã dùng một lần, hết hạn sau 15
  phút. Sau đó bạn nhận nhắc hạn và báo việc qua Zalo, và có thể:
    - hỏi chỉ đọc: "PO-2026-0101 đang ở đâu?", "DX-2026-041 BM04 ghi MOQ bao nhiêu?" (Zalo
      không bao giờ gửi giá hay nội dung chứng từ);
    - đề xuất sản phẩm bằng chat (vai Cung ứng, xem dưới);
    - duyệt bằng mã (vai duyệt, xem dưới).
- **Giờ** luôn là giờ Việt Nam.

## Cung ứng

- **Bước 1, đề xuất:** **Phát triển SP** → **Đề xuất sản phẩm** (tên, nhóm, ảnh). Bạn là PIC
  của hồ sơ. Nhiều sản phẩm một lúc: **Danh sách đề xuất** → tải file Excel; AI tách từng
  dòng, bạn đề xuất từng dòng. Qua Zalo: nhắn mô tả sản phẩm cho bot, trả lời các câu bot
  hỏi lại, kiểm tóm tắt, gõ **Đồng ý** để tạo hồ sơ (gõ **Bỏ đề xuất** để hủy).
- **Bước 2, lấy mẫu:** trên hồ sơ, **Yêu cầu mẫu** (tên NCC); thư gửi NCC AI soạn ở mục
  **Thư gửi NCC**: sửa nếu cần, gửi.
- **Bước 9, mã hàng:** xem mã hàng và SKU hệ thống đề xuất theo quy tắc công ty (mã trùng
  danh mục bị báo), **Trình ký**.
- **ĐẶT HÀNG:** khi đủ chữ ký, bấm **ĐẶT HÀNG**; hồ sơ PO mở ở "Chờ tạo PO".
- **Bước 10, tạo PO:** trên hồ sơ PO, xem PO nháp (dòng, số lượng, tổng do hệ thống tính),
  nhập số PO, **Tạo PO**.
- **Bước 11, đặt cọc:** tải PI của NCC; đề nghị đặt cọc AI soạn; chuyển Kế toán.
- **Bước 12:** duyệt mẫu màu; gửi gói cho MKT (BM04, HDSD, maquette); kiểm bản in thiết kế
  (điểm sai so với BM04 và luật nhãn được nêu, yêu cầu sửa AI soạn); nhận mẫu trước SX.
- **Bước 13:** tải lịch SX của NCC; ETD AI đọc hiện cạnh ô, bạn nhập ETD; thư hỏi tiến độ
  hằng tuần AI soạn, bạn gửi.
- **Bước 16:** tải hóa đơn; đề nghị thanh toán AI soạn (số còn phải trả do hệ thống tính).
- **Hằng ngày:** **Việc cần làm** (nhắc NCC, trễ SLA của bạn), **Cần chú ý**, **Control
  Tower**.

## R&D

- **Bước 3–5, test mẫu:** trên hồ sơ, **Đã nhận mẫu**; thẻ **Số đo vòng mẫu**: nhập từng
  tiêu chí (số hoặc Đạt/Không đạt). Hệ thống so với chuẩn của nhóm sản phẩm. Khi đủ số, AI
  soạn **Biên bản đánh giá mẫu** (bảng do hệ thống so, ghi chú có dẫn chứng) và, nếu có
  tiêu chí trượt, **Phiếu yêu cầu chỉnh sửa**. Chọn kết luận (Đạt / Cần chỉnh sửa / Hủy);
  gợi ý nằm bên cạnh. Vòng sau, từng mục phiếu cũ được kiểm: đã sửa / chưa / không kiểm
  được.
- **Bước 7, BM04:** mở BM04 AI điền sẵn từ báo giá và BM04 của NCC; ô thiếu hoặc mâu thuẫn
  để trống và được nêu; điền, **Hoàn tất BM04**.
- **Bước 12, test trước SX:** trên hồ sơ PO, thẻ **Số đo test trước SX**: nhập từng tiêu
  chí. Khi đủ, AI soạn **Biên bản test trước SX**. Bấm **Đạt test trước SX** hoặc **Không
  đạt test trước SX** (cần lý do), chọn "Biên bản AI soạn" làm chứng từ; hệ thống lưu biên
  bản với kết luận bạn chọn. Sửa số đo sau khi biên bản đã soạn thì phải dùng bản mới.

## TP Cung ứng

- **Bước 8:** xem email xác nhận AI soạn (kèm BM04, không giá) gửi NCC; khi NCC trả lời, tải
  email; điều khoản NCC đọc được so với BM04; **Đã thống nhất với NCC**.
- **Theo dõi:** **Bản tin hôm nay** (bấm AI tóm tắt: mỗi con số dẫn về hồ sơ), nhận leo
  thang khi hồ sơ quá hạn kéo dài; **Báo cáo** (tuần, điểm NCC, AI được duyệt).

## BGĐ

- **Bước 6, duyệt mẫu:** menu **Duyệt** (hoặc liên kết trong tin Zalo) → mở hồ sơ, đọc tờ
  trình AI soạn → **Duyệt** hoặc **Không duyệt** (kèm nhận xét).
- **Duyệt qua Zalo:** tin Zalo có tóm tắt và liên kết, không có mã. Mở liên kết trên cổng,
  xem hồ sơ; trang hiện mã dùng một lần. Trả lời bot **DUYỆT <mã>** hoặc **KHÔNG <mã> <lý
  do>**. Hồ sơ đổi sau khi bạn xem thì mã hết hiệu lực; bạn không duyệt được việc mình
  trình.
- **Bước 9, ký:** như bước 6, theo thứ tự ký của công ty.
- **Báo cáo:** **Báo cáo** → báo cáo tuần (số do hệ thống đếm, mỗi số nêu hồ sơ; **AI tóm
  tắt tuần** chỉ giữ câu đúng số), điểm NCC, tỷ lệ bản nháp AI được duyệt.

## Kế toán

- **Bước 9:** ký như BGĐ khi đến lượt.
- **Bước 11, 16:** trên hồ sơ PO, xem đề nghị đặt cọc / thanh toán AI soạn; tải UNC; nhập
  số tiền đã chuyển (ô trống, số đề nghị nằm bên cạnh); duyệt. Tài khoản thụ hưởng khác
  tài khoản đã lưu của NCC được cảnh báo.

## QC

- **Bước 14:** tải báo cáo QC; hệ thống gợi ý Đạt / Không đạt theo số lỗi so với mức chấp
  nhận (AQL), không theo chữ "PASS" trong báo cáo. Chọn kết quả; **Không đạt** cần lý do
  và kèm phiếu yêu cầu làm lại AI soạn; **Đạt** nhập số container.

## Logistics

- **Bước 15:** tải packing list, B/L, giấy báo hàng đến, C/O. Số lượng theo SKU được so với
  PO; ETA ghi vào hồ sơ; hồ sơ hải quan còn thiếu được liệt kê. Duyệt **Xác nhận hàng đến cảng**.

## Kho

- **Bước 17:** phiếu nhập kho AI soạn từ dòng PO và packing list. Nhập **số đếm** từng dòng
  (ô trống, số giao nằm bên cạnh); dòng nào chưa có số thì không duyệt được. Dòng lệch được
  nêu; biên bản chênh lệch và thư khiếu nại NCC được soạn để người gửi.

## MKT

- **Bước 12:** khi Cung ứng gửi gói (BM04, HDSD, maquette), bạn nhận thông báo. Tải nội dung
  bao bì lên hồ sơ PO, **Nộp nội dung bao bì**. Bạn không thấy giá và không làm bước của
  Cung ứng.

## Quản trị quy trình

- **Cấu hình:** SLA theo nhóm sản phẩm, danh sách nhóm, nhiệm vụ theo bước, tiêu chí test
  mẫu, quy tắc mã hàng, luật bao bì, mẫu biểu chứng từ. Mỗi thay đổi có lịch sử.

## Quản trị công ty

- **Nạp dữ liệu** (một lần khi bắt đầu): **Nạp dữ liệu** → **Tải mẫu** → điền các sheet NCC,
  Danh mục, Người dùng, Hồ sơ SP, Hồ sơ PO → **Chạy thử** (không ghi gì, xem từng dòng: sẽ
  thêm / đã có / từ chối kèm lý do) → **Nạp thật**. Nạp lại cùng file không tạo trùng. Hồ
  sơ đang chạy mở đúng bước với một dòng lịch sử "Nạp từ dữ liệu cũ" và ngày khai báo; PIC
  phải là thành viên workspace.
- **Thành viên và vai:** trang quản trị; một người có thể giữ nhiều vai, nhưng người trình
  không tự duyệt.
