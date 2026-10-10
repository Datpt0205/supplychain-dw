# Danh sách nghiệm thu Elmich

Bản 2026-10-10, ticket UAT-1. Mỗi dòng là một điều bộ slide PoC (`poc-slides-brief.md`)
hoặc quy trình 17 bước (`process.md`) hứa, ticket làm nó (đường dẫn tính từ
`.claude/plans/supply-chain/`), cách kiểm (mã kịch bản ở `uat-plan.md`), và ô kết quả để
người kiểm của Elmich điền. "Đạt" chỉ khi mọi kiểm âm của dòng đó bị từ chối.

## Theo slide

| Slide | Điều hứa                                                                                              | Ticket                                                       | Cách kiểm                     | Kết quả (Đạt / Không / Ghi chú) | Người kiểm |
| ----- | ----------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ----------------------------- | ------------------------------- | ---------- |
| 1     | Bước 1–10 chạy trên Digital Worker, từ đề xuất tới tạo PO                                             | `stage-1/issues/01`–`05`                                     | 1.1–10.1                      |                                 |            |
| 2     | Người tạo đề xuất là PIC; đổi PIC có lý do và lịch sử                                                 | `stage-1/issues/01`, `06`                                    | 1.1; đổi PIC trên trang hồ sơ |                                 |            |
| 2     | Đề xuất qua web hoặc chat Zalo                                                                        | `stage-1/issues/01`, `zalo-channel/issues/04`                | 1.1, 1.3                      |                                 |            |
| 2     | PIC nhận nhắc hạn, quá hạn kéo dài báo TP Cung ứng                                                    | `stage-1/issues/06`                                          | C5                            |                                 |            |
| 3     | Web và Zalo cùng một hồ sơ, cùng lịch sử                                                              | `zalo-channel/issues/04`–`06`                                | 1.3, 6.2, C2                  |                                 |            |
| 4     | Login theo vai (Cung ứng, R&D, TP Cung ứng, BGĐ, Kế toán)                                             | `personal-settings/issues/01`, `stage-1/issues/01`           | C8                            |                                 |            |
| 4     | Hồ sơ bước 1–10 có SLA và vòng chỉnh sửa mẫu                                                          | `stage-1/issues/01`, `06`                                    | 3.1–5.1                       |                                 |            |
| 4, 8  | Bản nháp: phiếu chỉnh sửa, tờ trình, BM04, email chốt NCC, PO nháp                                    | `ai-automation/issues/03`, `09`–`12`, `14`                   | 4.1, 6.1, 7.1, 8.1, 10.1      |                                 |            |
| 4, 8  | Kiểm trùng mã hàng/SKU bằng đối chiếu danh mục                                                        | `ai-automation/issues/13`, `onboarding/issues/01`            | 9.1                           |                                 |            |
| 4     | Nút ĐẶT HÀNG                                                                                          | `stage-1/issues/05`                                          | 9.2                           |                                 |            |
| 4     | Trang Cài đặt để liên kết Zalo                                                                        | `personal-settings/issues/01`, `zalo-channel/issues/01`      | C1                            |                                 |            |
| 5     | Liên kết Zalo; nhắc hạn, báo việc, báo cáo test mẫu cuối ngày                                         | `zalo-channel/issues/01`, `02`; `stage-1/issues/08`          | C1, C4, C5                    |                                 |            |
| 6     | Chat đề xuất nhiều lượt: bot hỏi lại, tóm tắt, chỉ tạo khi "Đồng ý"; không hiểu thì báo chưa hiểu     | `zalo-channel/issues/04`                                     | 1.3                           |                                 |            |
| 6     | Hỏi tình trạng mẫu trên Zalo                                                                          | `zalo-channel/issues/06`, `ai-automation/issues/19`          | C2                            |                                 |            |
| 7     | Duyệt trên Zalo chỉ sau khi xem đúng phiên bản trên cổng; mã dùng một lần; người trình không tự duyệt | `zalo-channel/issues/05`, `approval-decider-scope/issues/01` | 6.2, C8                       |                                 |            |
| 8     | BM04 đánh dấu ô thiếu hoặc mâu thuẫn, không tự điền đoán                                              | `ai-automation/issues/11`                                    | 7.1                           |                                 |            |
| 9     | Bản tin cuối ngày cho TP Cung ứng, mỗi con số dẫn về hồ sơ                                            | `stage-1/issues/08`                                          | C4                            |                                 |            |
| 11    | Hạ tầng cloud (webhook HTTPS) hoặc máy chủ Elmich (bot tự hỏi tin)                                    | `hosting/issues/01`, `02`; `zalo-channel/issues/03`, `07`    | Kiểm khi dựng môi trường      |                                 |            |
| 13    | Báo cáo đo kết quả, thêm tỷ lệ việc làm qua Zalo                                                      | `ai-automation/issues/20`                                    | C6 (tỷ lệ qua Zalo: chưa đo)  |                                 |            |

## Theo quy trình (bước 11–17 và phần AI)

| Bước / chủ đề   | Điều hứa                                                                                               | Ticket                            | Cách kiểm      | Kết quả           | Người kiểm |
| --------------- | ------------------------------------------------------------------------------------------------------ | --------------------------------- | -------------- | ----------------- | ---------- |
| 11, 16          | Đề nghị đặt cọc và thanh toán AI soạn từ chứng từ NCC; số tiền do hệ thống tính; tài khoản lạ cảnh báo | `ai-automation/issues/15`         | 11.1, 16.1     |                   |            |
| 12              | MKT nộp nội dung bao bì; AI đọc bản in, hệ thống so BM04 và luật nhãn                                  | `ai-automation/issues/16`         | 12.1           |                   |            |
| 12              | Biên bản test trước SX AI soạn từ số đo của R&D; thành chứng từ khi R&D chọn bước                      | `ai-automation/issues/17` (mục 2) | 12.2           |                   |            |
| 13–15           | Lịch SX, báo cáo QC, packing list, B/L, giấy báo hàng đến, C/O được đọc; QC gợi ý theo số              | `ai-automation/issues/17`         | 13.1–15.1      |                   |            |
| 17              | Kho đếm từng dòng; dòng lệch thành biên bản và thư khiếu nại                                           | `ai-automation/issues/18`         | 17.1           |                   |            |
| Trợ lý hồ sơ    | Hỏi về nội dung hồ sơ, trả lời có nguồn, không rộng hơn quyền người hỏi                                | `ai-automation/issues/19`         | C2, C3         |                   |            |
| Báo cáo         | Báo cáo tuần BGĐ, điểm NCC, AI được duyệt bao nhiêu                                                    | `ai-automation/issues/20`         | C6             |                   |            |
| Nạp dữ liệu     | NCC, danh mục, người dùng, hồ sơ đang chạy; chạy thử; nạp lại không trùng                              | `onboarding/issues/01`, `02`      | C7             |                   |            |
| An toàn AI      | File chèn lệnh không làm bước tự chuyển; AI không bịa số                                               | `ai-automation/issues/06`         | C9             |                   |            |
| Ảnh và PDF quét | Chưa đọc được (OCR chưa đạt tiếng Việt); trang nói rõ, người kiểm bằng mắt                             | `ai-automation/issues/21`         | Tải một ảnh PI | Ghi nhận giới hạn |            |

## Ký nghiệm thu

| Vai             | Họ tên | Ngày | Chữ ký |
| --------------- | ------ | ---- | ------ |
| Đại diện Elmich |        |      |        |
| FDX             |        |      |        |
