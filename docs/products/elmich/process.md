# Quy trình cung ứng Elmich (Phần A)

> Chép từ `Quy trinh Quan ly Cung ung Elmich.pdf` (bản gửi ngày 5/10/2026, cùng nội
> dung với `Quy trinh Quan ly Cung ung Elmich.docx` ngày 23/9/2026; cả hai không nằm
> trong repo) sang markdown ngày 5/10/2026. Mục 1–3 giữ nguyên văn, kể cả các dấu
> **[Cần xác nhận]** của Elmich. Mục 4–6 là phần của repo: đối chiếu từng bước với
> code và ticket, những chỗ nguồn tự mâu thuẫn, và danh sách điểm mở.
>
> Đây là đặc tả sống của sản phẩm. Khi Elmich trả lời một điểm mở, sửa mục 4–6 và
> ghi ngày; mục 1–3 chỉ đổi khi Elmich gửi bản mới. Trạng thái của từng câu hỏi nằm
> ở `.claude/plans/supply-chain.md`, mục "Decisions owed"; file này không giữ bản
> thứ hai.
>
> **Phần B (Quản lý sản xuất Profile, luồng Product MKT, Media, Content) nằm ngoài
> phạm vi** theo quyết định của Đạt ngày 5/10/2026, nên không được chép.

**QUY TRÌNH QUẢN LÝ CUNG ỨNG & PHÁT TRIỂN SẢN PHẨM · ELMICH · PHẦN A**

## 1. Quy trình tổng quan

Quy trình gồm 2 luồng nối tiếp: Phát triển sản phẩm mới (bước 1–4) → Theo dõi đơn
hàng (bước 5–6).

1. Chọn SP phát triển → 2. Lấy mẫu NCC & duyệt mẫu → 3. Chốt SP & Profile → 4. Tạo
   mã hàng hóa → 5. Đặt hàng & phê duyệt → 6. Theo dõi đơn hàng

| Step                          | Phòng ban                   | Action                                                                         | Input                                              | Output                                         | SLA                                                                                       |
| ----------------------------- | --------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| 1. Chọn SP phát triển         | Cung ứng                    | Đề xuất SP mới; xác định mã hàng mẫu; đánh giá ưu tiên theo Category           | Insight thị trường, nhu cầu KD, ý tưởng SP mới     | Danh sách SP đề xuất + mức độ ưu tiên          | Theo đặc thù từng cate hàng                                                               |
| 2. Lấy mẫu từ NCC & Duyệt mẫu | Cung ứng, R&D, Ban Giám đốc | Lấy mẫu từ NCC; test/đánh giá mẫu; yêu cầu chỉnh sửa (nếu có); trình duyệt BGĐ | Mẫu SP từ NCC, tiêu chuẩn kỹ thuật & chất lượng    | Mẫu đạt yêu cầu + quyết định duyệt/không duyệt | Phụ thuộc quy trình từng NCC                                                              |
| 3. Chốt SP & Profile          | R&D, Cung ứng               | Lập Biểu mẫu 04 (Profile SP); thống nhất với NCC; chốt thông tin SP            | Mẫu đã duyệt, kết quả test, thông tin NCC          | Profile SP hoàn chỉnh (BM04)                   | 02 ngày làm BM04                                                                          |
| 4. Tạo mã hàng hóa            | Cung ứng                    | Tạo mã hàng hóa chính thức                                                     | Profile SP (BM04), thông tin SP đã chốt            | Mã hàng chính thức                             | 0,5 ngày                                                                                  |
| 5. Đặt hàng & Phê duyệt       | Cung ứng, Ban Giám đốc      | Trình ký đơn mới; tạo PO; phê duyệt PO; đặt cọc                                | Mã hàng chính thức, kế hoạch mua hàng, báo giá NCC | PO được phê duyệt, đơn hàng hợp lệ             | **[Cần xác nhận]** chưa có quy định thời gian duyệt chứng từ các cấp & thanh toán đặt cọc |
| 6. Theo dõi đơn hàng          | Cung ứng                    | Theo dõi tiến độ SX/giao hàng; cập nhật trạng thái; xử lý phát sinh            | PO đã duyệt, lịch giao hàng                        | Đơn hàng hoàn thành/sẵn sàng nhập kho          | Theo đặc thù cate hàng & từng NCC                                                         |

Lưu ý: bước 6 "Theo dõi đơn hàng" thực chất là một chuỗi con gồm 10 bước (thiết kế
bao bì, sản xuất, QC, đóng cont, thanh toán, nhập kho) — xem chi tiết ở mục "3. Quy
trình chi tiết" bên dưới.

## 2. Danh mục tài liệu/chứng từ nghiệp vụ chính

| Tài liệu/Chứng từ                      | Mô tả                                               | Bộ phận lập   | Bộ phận nhận/sử dụng tiếp theo |
| -------------------------------------- | --------------------------------------------------- | ------------- | ------------------------------ |
| Danh sách SP đề xuất                   | Đề xuất sản phẩm mới cần phát triển                 | Cung ứng      | R&D                            |
| Mẫu sản phẩm                           | Mẫu vật lý/ảnh sản phẩm từ NCC                      | Nhà cung cấp  | R&D                            |
| Biên bản đánh giá mẫu                  | Kết quả test kỹ thuật/chất lượng mẫu                | R&D           | Cung ứng, Ban Giám đốc         |
| Phiếu yêu cầu chỉnh sửa mẫu            | Yêu cầu NCC sửa mẫu khi không đạt                   | R&D           | Cung ứng → Nhà cung cấp        |
| Profile sản phẩm (BM04)                | Hồ sơ kỹ thuật SP đã chốt (thông số, hình ảnh, giá) | R&D           | Cung ứng, Ban Giám đốc         |
| Mã hàng hóa chính thức                 | Mã định danh SP dùng để đặt hàng                    | Cung ứng      | Ban Giám đốc, Kế toán          |
| Đơn đặt hàng (PO)                      | Chứng từ đặt hàng với NCC                           | Cung ứng      | Ban Giám đốc (duyệt), Kế toán  |
| Hồ sơ đặt cọc/thanh toán               | Chứng từ tài chính theo tiến độ đơn hàng            | Cung ứng      | Kế toán                        |
| Nội dung bao bì / Sách HDSD / Maquette | Tài liệu thiết kế bao bì                            | Cung ứng, MKT | Bộ phận thiết kế, Nhà cung cấp |

**[Cần xác nhận]** cột "Bộ phận lập"/"Bộ phận nhận" ở trên được suy luận từ mô tả
quy trình — cần Elmich xác nhận lại cho đúng thực tế.

## 3. Quy trình chi tiết (Input/Output từng bước)

Bảng dưới hợp nhất 17 bước, nêu rõ từng bước do bộ phận nào thực hiện và bàn giao
kết quả cho bộ phận/bước nào tiếp theo — nhờ đối chiếu thêm với tài liệu nội bộ, 4
bước trước đây bị bỏ trống (Đặt cọc, Theo dõi đơn hàng, Thanh toán, Tiếp nhận hàng
về) đã có dữ liệu.

**Quy tắc gán PIC (cập nhật mới):** Người phụ trách (PIC) cho một sản phẩm/SKU là
nhân sự trực tiếp tạo mới sản phẩm đó ở bước 1, được gán mặc định và áp dụng xuyên
suốt các bước sau. Quy tắc này thay thế cách phân PIC theo Category trước đây.

### 3.1 Sơ đồ chi tiết

1 → 2 → 3 → 7 → 8 → 9 → 10 → 11 (Đặt cọc) → 12 (Thiết kế màu/bao bì) → 13 → 14 →
15 (Hàng về cảng) → 16 (Thanh toán) → 17 (Nhập kho)

Nhánh rẽ ở bước 3: kết quả "Cần chỉnh sửa" → quay lại bước 2 lấy mẫu (lặp đến khi
đạt); kết quả "Hủy" → dừng hẳn quy trình.

Sơ đồ con — Thiết kế màu sắc & bao bì (trong bước 12): Thiết kế lên màu sắc → Gửi
NCC làm mẫu màu → Cung ứng duyệt màu → báo Trưởng phòng MKT → Gửi MKT (BM04 + HDSD
\+ maquette) → MKT lên nội dung bao bì → Thiết kế làm bao bì → Cung ứng duyệt thiết
kế → Gửi NCC làm bao bì → Lấy mẫu trước SX → R&D test trước SX

Hai sơ đồ trên là phần "Sơ đồ chi tiết" mà file khảo sát gốc để trống, được vẽ lại
từ bảng Input/Output bên dưới. **[Cần xác nhận]** lại với bộ phận Cung ứng trước khi
gửi bản chính thức.

### 3.2 Bảng 17 bước

| Step | Bộ phận thực hiện       | Bàn giao cho                               | Action                                                                                           | Input                                      | Output                                                                                                                                 |
| ---- | ----------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------ | ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Cung ứng                | R&D                                        | Chọn SP phát triển                                                                               | Insight thị trường, nhu cầu KD, ý tưởng SP | Danh sách SP đề xuất (mã+tên SP, hình ảnh)                                                                                             |
| 2    | Cung ứng                | R&D                                        | Liên hệ NCC và lấy mẫu                                                                           | SP được chọn phát triển                    | Mẫu SP từ NCC (ảnh/thông tin)                                                                                                          |
| 3    | R&D                     | Cung ứng hoặc Ban Giám đốc                 | Test, đánh giá mẫu                                                                               | Mẫu SP, tiêu chuẩn kỹ thuật                | Đạt (báo cáo hàng ngày cho Trưởng phòng Cung ứng) / Không đạt-Cần chỉnh sửa (quay lại bước 2) / Không đạt-Hủy (dừng quy trình) + lý do |
| 4    | R&D                     | Cung ứng → Nhà cung cấp                    | Lập yêu cầu chỉnh sửa mẫu (nếu không đạt)                                                        | Kết quả test không đạt                     | Phiếu yêu cầu chỉnh sửa mẫu                                                                                                            |
| 5    | R&D                     | R&D (lặp lại bước 3)                       | Nhận mẫu chỉnh sửa, test lại                                                                     | Mẫu chỉnh sửa từ NCC                       | Mẫu đạt yêu cầu                                                                                                                        |
| 6    | R&D                     | Ban Giám đốc                               | Trình mẫu lên Ban Giám đốc                                                                       | Mẫu đạt, biên bản đánh giá                 | Quyết định duyệt/không duyệt                                                                                                           |
| 7    | R&D, Cung ứng           | Nhà cung cấp                               | Lập Profile SP (BM04)                                                                            | Mẫu được duyệt                             | Profile SP hoàn chỉnh (BM04) — SLA 4 ngày                                                                                              |
| 8    | Cung ứng                | Trưởng phòng Cung ứng (xác nhận qua email) | Thống nhất SP chốt với NCC                                                                       | Profile SP (BM04)                          | SP được chốt, sẵn sàng đặt hàng — SLA 5 ngày                                                                                           |
| 9    | Cung ứng                | Ban Giám đốc, Kế toán                      | Tạo mã hàng hóa, chốt số lượng SKU của sản phẩm (kiểm tra không trùng mã hàng/mã SKU) + trình ký | Profile SP (BM04), hồ sơ được duyệt        | Mã hàng chính thức + SKU (SKU chỉ thêm sau khi có mã hàng chính thức)                                                                  |
| 10   | Cung ứng                | Kế toán                                    | Tạo đơn đặt hàng (PO)                                                                            | Mã hàng, báo giá NCC                       | PO được tạo, phân loại Hàng mới / Hàng đặt lại                                                                                         |
| 11   | Cung ứng, Kế toán       | Nhà cung cấp                               | Đặt cọc                                                                                          | PO đã tạo                                  | Xác nhận đã đặt cọc — SLA 10 ngày                                                                                                      |
| 12   | Cung ứng, MKT, Thiết kế | Nhà cung cấp                               | Theo dõi đơn hàng — thiết kế màu sắc & bao bì                                                    | Ảnh 3D, mẫu thực tế                        | Màu/bao bì được duyệt (xem sơ đồ con); màu đạt được báo cho Trưởng phòng MKT trước khi chuyển bao bì                                   |
| 13   | Cung ứng, R&D           | Nhà cung cấp                               | Test trước SX & sản xuất                                                                         | Mẫu trước SX                               | Sản phẩm sản xuất xong                                                                                                                 |
| 14   | Cung ứng, QC            | Cung ứng (vận tải)                         | QC kiểm hàng, đóng cont                                                                          | Kết quả test sau SX                        | Hàng đóng cont, sẵn sàng vận chuyển                                                                                                    |
| 15   | Cung ứng                | Kế toán                                    | Tiếp nhận hàng về cảng                                                                           | Hàng đã đóng cont                          | Hàng về cảng — SLA 21 ngày                                                                                                             |
| 16   | Cung ứng, Kế toán       | Kho                                        | Thanh toán                                                                                       | Hàng về cảng                               | Hồ sơ thanh toán hoàn tất — SLA 10 ngày                                                                                                |
| 17   | Cung ứng, Kho           | —                                          | Nhập kho                                                                                         | Hồ sơ thanh toán hoàn tất                  | Đơn hàng hoàn thành — SLA 5 ngày                                                                                                       |

SLA cụ thể (số ngày) đã nhắc ở từng dòng trong bảng trên (BM04, chốt SP, đặt cọc,
hàng về cảng, thanh toán, nhập kho); các bước còn lại áp dụng SLA theo Cate (biến
động theo danh mục sản phẩm). **[Cần xác nhận]** các con số này lấy từ tài liệu
thiết kế giải pháp mới nhất, cần Cung ứng xác nhận đã áp dụng thực tế hay còn là đề
xuất.

## 4. Đối chiếu với code và ticket

Hai giai đoạn của sơ đồ phụ lục trong PDF ("Giai đoạn 1: Phát triển sản phẩm →
Giai đoạn 2: Theo dõi cung ứng") là hai aggregate trong `dw_supply_chain`:
**Hồ sơ phát triển sản phẩm** (`ProductDevelopmentCase`, bước 1–9, chưa có code) và
**Hồ sơ PO** (`POCase`, bước 10–17, đã có từ nhánh lưu trữ). Bàn giao giữa hai giai
đoạn là nút **ĐẶT HÀNG** ([ADR 0017](../../../packages/python/dw_supply_chain/docs/adr/0017-e7-hand-off-via-order-requested.md)),
nên 17 bước là một luồng liền. Tên và nhãn trạng thái theo glossary
`packages/python/dw_supply_chain/CONTEXT.md`.

| Bước | Trạng thái đích (code)                                                       | Ai bấm (duty hoặc người duyệt)                                                                 | Ticket                                                                            | Hiện trạng                                                                           | AI chuẩn bị (người duyệt chuyển bước, ADR 0025)                                                                                                                                                    |
| ---- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | `proposed`; PIC đóng dấu từ người tạo                                        | `ordering` (Cung ứng), trên cổng hoặc chat Zalo                                                | `stage-1/issues/01`, `case-documents/issues/01`, `zalo-channel/issues/04`         | Chưa có                                                                              | Danh sách đề xuất (`ai-automation/issues/08`): AI tách file thành từng dòng có trích dẫn, kiểm trùng mã, gợi ý nhóm; PIC đề xuất từng dòng                                                         |
| 2    | `sample_requested`                                                           | `ordering`                                                                                     | `stage-1/issues/01`                                                               | Chưa có                                                                              | Thư gửi NCC (`07`, Elmich): AI soạn đề nghị gửi mẫu khi vào bước, nhắc NCC khi quá hạn mẫu; người sao chép, tự gửi, bấm "Đã gửi"                                                                   |
| 3    | `sample_testing` → `pending_bod_review` / `revision_requested` / `cancelled` | `rnd` (R&D)                                                                                    | `stage-1/issues/01`, `stage-1/issues/07`                                          | Chưa có                                                                              | Đề xuất bước (`05`, `09`, Elmich): R&D nhập số đo theo tiêu chí; AI soạn biên bản (bảng của code) và phiếu; R&D chọn Đạt / Cần chỉnh sửa / Hủy rồi duyệt                                           |
| 4    | `revision_requested`, kèm Phiếu yêu cầu chỉnh sửa                            | `rnd`                                                                                          | `stage-1/issues/01`, `case-documents/issues/01`                                   | Chưa có                                                                              | Phiếu nháp (`09`): mỗi tiêu chí trượt một mục, yêu cầu AI viết có dẫn chứng; duyệt Cần chỉnh sửa thì gửi NCC qua thư AI soạn                                                                       |
| 5    | `sample_testing`, vòng mẫu + 1                                               | `rnd`                                                                                          | `stage-1/issues/01`                                                               | Chưa có                                                                              | Kiểm vòng (`09`): mỗi mục phiếu vòng trước thành đã sửa / chưa sửa / không kiểm được theo số đo vòng mới                                                                                           |
| 6    | `profile_in_progress` / `cancelled`                                          | duyệt `supply_chain.product_action.bod_review` (BGĐ), trên web hoặc Zalo sau khi xem trên cổng | `stage-1/issues/02`, `approval-decider-scope/issues/01`, `zalo-channel/issues/05` | Cơ chế duyệt có, chưa giới hạn người quyết                                           | Tờ trình (`10`, Elmich): AI soạn trước khi trình BGĐ, câu nào cũng dẫn hồ sơ, lịch sử, biên bản; giá ẩn với người không có quyền                                                                   |
| 7    | `supplier_confirmation`, kèm BM04                                            | `rnd`                                                                                          | `stage-1/issues/03`                                                               | Khóa SLA `bm04` có trong policy nhưng không trạng thái nào đọc                       | BM04 là biểu mẫu trong app, giá sau scope (`01`, xong). AI điền (`11`, Elmich): mỗi ô dẫn nguồn, giá do code chép từ báo giá, hai nguồn khác nhau là mâu thuẫn để trống; duyệt ghi phiên bản hồ sơ |
| 8    | `item_coding`, kèm email xác nhận                                            | `supply_lead` (TP Cung ứng)                                                                    | `stage-1/issues/03`                                                               | Khóa SLA `supplier_confirmation` có nhưng không ai đọc                               | Đề xuất bước (`05`, Elmich): đọc email NCC đã tải lên, TP Cung ứng duyệt trên web hoặc Zalo. Email chốt soạn từ BM04 đã duyệt, so từng điều khoản thư trả lời với BM04 (`12`, Elmich)              |
| 9    | `pending_signoff` → `ready_to_order`                                         | `ordering`; trình ký BGĐ, Kế toán, trên web hoặc Zalo sau khi xem trên cổng                    | `stage-1/issues/04`, `zalo-channel/issues/05`                                     | Chưa có mã hàng, SKU                                                                 | Chưa; đề xuất mã hàng, SKU, kiểm danh mục đã nạp, tờ trình ký (`13`)                                                                                                                               |
| 9→10 | hồ sơ phát triển `ordered`; hồ sơ PO mới ở `order_requested`                 | `ordering` (nút ĐẶT HÀNG)                                                                      | `stage-1/issues/05`                                                               | Chưa có                                                                              | Người bấm                                                                                                                                                                                          |
| 10   | `po_created`, có số PO và loại đơn                                           | `ordering`; báo Kế toán                                                                        | `stage-1/issues/05`                                                               | `CreatePOCase` có; thiếu loại đơn, SKU, dòng PO                                      | Đơn giá, điều khoản, tổng do code tính có (`01`); chưa: AI soạn PO (`14`)                                                                                                                          |
| 11   | `waiting_deposit` → `deposit_confirmed`                                      | `ordering`, rồi `finance`                                                                      | `port/issues/01`                                                                  | Có; đồng hồ SLA chạy từ lúc vào `waiting_deposit`, chưa có chứng từ cọc              | Số tiền cọc, tài khoản NCC có (`01`); chưa: AI soạn đề nghị, đọc PI, UNC (`15`)                                                                                                                    |
| 12   | `pre_production`; bốn bước con trên Hồ sơ PO                                 | duyệt màu, duyệt thiết kế, nhận mẫu trước SX: `ordering`; test trước SX: `rnd`                 | `packaging-design/issues/01`                                                      | Có (PK); phần MKT, Thiết kế của sơ đồ con chờ Phần B, "báo TP MKT" là dòng lịch sử   | Chưa; MKT tối thiểu, AI kiểm bản in với BM04 và luật nhãn (`16`)                                                                                                                                   |
| 13   | `production`                                                                 | `ordering`; khi tenant bật luật, chỉ sau khi R&D đạt test trước SX                             | `port/issues/01`, `packaging-design/issues/01`                                    | Có; test trước SX có biên bản (PK), Elmich bật luật; chưa có SLA                     | Chưa; AI soạn biên bản test trước SX, đọc lịch SX (`17`)                                                                                                                                           |
| 14   | `qc` → `in_transit` (hoặc `rework`)                                          | `ordering`, rồi `qc`                                                                           | `port/issues/01`                                                                  | Có; đóng cont không phải bước riêng                                                  | Chưa; AI đọc báo cáo QC, packing list (`17`)                                                                                                                                                       |
| 15   | `arrived_port`                                                               | `logistics`                                                                                    | `port/issues/01`                                                                  | Có; SLA `port_arrival` chạy từ lúc vào `in_transit`                                  | Chưa; AI đọc B/L, giấy báo hàng đến, kiểm hồ sơ hải quan (`17`)                                                                                                                                    |
| 16   | `waiting_payment` → `payment_completed`                                      | `ordering`, rồi `finance`                                                                      | `port/issues/01`                                                                  | Có; chưa có chứng từ thanh toán                                                      | Chưa; AI soạn đề nghị thanh toán, đối chiếu PO, hóa đơn (`15`)                                                                                                                                     |
| 17   | `warehouse_receiving` → `completed`                                          | `warehouse`                                                                                    | `port/issues/01`                                                                  | Có; SLA `warehouse_receipt` đo từ thanh toán tới lúc hàng vào kho (`completed`), HR3 | Chưa; AI soạn phiếu nhập kho, đối chiếu số đếm (`18`)                                                                                                                                              |

**Zalo hai chiều** (Đạt, 5/10/2026): ở bước 1, PIC đề xuất được bằng chat Zalo; bot hỏi
trường còn thiếu, tóm tắt và chỉ tạo hồ sơ khi người đó trả lời "Đồng ý", ảnh gửi kèm
thành `product_image` (`zalo-channel/issues/04`). Ở bước 6 và bước 9, người duyệt quyết
được bằng Zalo, nhưng chỉ sau khi mở đúng phiên bản hồ sơ trên cổng và gõ lại mã dùng
một lần thấy ở đó; tin Zalo không mang mã
([ADR 0014](../../adr/0014-e4-decisions-on-zalo-after-a-portal-view.md),
`zalo-channel/issues/05`). Mọi người hỏi được chỉ đọc về hồ sơ họ được xem
(`zalo-channel/issues/06`). Quyết và thao tác trên web không đổi.

SLA theo Category và thông báo cho PIC: `stage-1/issues/06`. Chứng từ của mọi bước:
`case-documents/issues/01`; ảnh SP ở bước 1 là `product_image`, nhận tùy chọn lúc
`propose` (S1 bước 4). Báo cáo hằng ngày cho TP Cung ứng ở bước 3 là một nhóm của
daily brief (`stage-1/issues/08`, dạng và kênh chờ QE-19). Bước 13–17 có trên `main`
qua lát port (`port/issues/01`). Đóng cont thành bước riêng (bước 14) hoãn tới khi
Elmich trả lời QE-15 (QO-6). Đường dẫn ticket tính từ `.claude/plans/supply-chain/`.

**AI chuẩn bị, người duyệt** (Đạt, 9/10/2026; ADR 0025 E14): ở mỗi bước AI soạn chứng từ, đọc file NCC kéo vào hồ sơ, chạy phép kiểm, rồi trình "chuyển sang bước X với chứng từ Y"; bước chỉ đổi khi người có duty duyệt. Việc vật lý (test, trả tiền, đếm hàng) do người nhập kết quả, AI chỉ gợi ý cạnh ô trống. Tin gửi NCC do AI soạn, người gửi (E18). Ticket của cột AI nằm ở `ai-automation/issues/`. Lane trích xuất chứng từ (`02`, xong 9/10/2026): biên bản đánh giá mẫu, email xác nhận NCC, BM04 tải lên và báo giá NCC (`supplier_quotation`, loại chứng từ mới) được đọc thành trường có trích dẫn nguyên văn; ô không có trích dẫn tìm thấy là khoảng trống; số tài khoản bị che trước khi gọi mô hình; ảnh và MSG chưa đọc. Bản nháp và mẫu chứng từ (`03`, xong 9/10/2026): năm mẫu trung tính (phiếu chỉnh sửa, biên bản đánh giá, tờ trình BGĐ, BM04, PO), mẫu riêng của tenant tải lên không cần deploy, khối "Bản nháp chứng từ" trên trang hồ sơ (ô thiếu, nguồn từng trường, xem trước, sửa thành phiên bản mới, từ chối có lý do); bản nháp không bao giờ thỏa chứng từ của bước. Skill (`04`, xong 9/10/2026): kiến thức quy trình (17 bước, chứng từ từng bước, hướng dẫn BM04, đọc biên bản đánh giá, luật nhãn theo NĐ 43/2017 và 111/2021, AQL, hồ sơ hải quan, giọng thư NCC) là artifact có phiên bản, tenant ghi đè được, prompt khai và hệ thống đặt vào phần system; bốn skill sau cùng chờ prompt của `07`, `16`, `17`. Đề xuất bước (`05`, xong 9/10/2026): khi hồ sơ vào bước có trong policy `supply_chain_step_preparation` của tenant (nền tảng: không bước nào; Elmich: bước 3–5 test mẫu và bước 8 chốt NCC), một run soạn bản nháp, đọc chứng từ nguồn, chạy phép kiểm và trình một approval `supply_chain.step_proposal.<hành động>` đóng dấu scope duty của bước; duyệt (người có duty, kèm nhận xét) thì bản nháp thành chứng từ `ai_prepared` và bước được áp trong một giao dịch; hồ sơ, bản nháp hay chứng từ nguồn đổi sau khi trình thì quyết định bị 409 và đề xuất được chuẩn bị lại; bước vật lý chỉ duyệt trên web sau khi nhập kết quả; khối "AI đã chuẩn bị" trên trang hồ sơ nêu cả lý do khi chưa chuẩn bị được. Eval chuẩn bị và cổng mô hình (`06`, xong 9/10/2026): mỗi tác vụ đọc và soạn có ca đúng, chèn lệnh, xuyên tenant/workspace, thiếu bằng chứng, số bịa, mâu thuẫn, file không đọc được; một tác vụ chỉ chuyển sang Qwen khi lần chạy thật của cổng qua đủ ca của nó (chưa có lần chạy thật nào). Thư gửi NCC (`07`, xong 9/10/2026): AI soạn thân thư (đề nghị gửi mẫu khi hồ sơ vào bước 2, nhắc NCC khi follow-up phía NCC mở, xác nhận sản phẩm khi vào bước 8; Elmich bật cả ba), code viết tiêu đề, lời chào, hạn phản hồi và lời kết từ mẫu có phiên bản và chỉ giữ đoạn mà mọi con số có trong bằng chứng được dẫn; không giá, không số tài khoản; người sao chép (hoặc mở ứng dụng thư), tự gửi từ hộp thư của mình và bấm "Đã gửi"; hệ thống không gửi gì, PIC được báo có bản nháp, không kèm nội dung. Danh sách SP đề xuất (`08`, xong 9/10/2026): PIC tải một file (PDF có chữ, DOCX, XLSX, EML; ảnh và bản quét thì máy không đọc), lane đọc thành từng dòng (tên, mã đề xuất, NCC, mã hàng, ảnh tham chiếu) có trích dẫn nguyên văn, gợi ý nhóm sản phẩm (chỉ nhận khóa trong danh sách của công ty) và mức ưu tiên kèm lý do, nêu mã đã có hồ sơ, mã lặp trong danh sách, mã hàng/SKU đã cấp, tên trùng hồ sơ cũ; PIC sửa ô, chọn nhóm rồi đề xuất (thành hồ sơ qua đúng cửa `propose`, PIC là người bấm, mã trùng vẫn bị cơ sở dữ liệu từ chối) hoặc bỏ từng dòng; không có nút làm cả danh sách. Bước 3–5 có số đo (`09`, xong 9/10/2026): R&D nhập số đo theo tiêu chí của nhóm sản phẩm (policy `supply_chain_sample_criteria`, tenant ghi đè; Elmich: ngưỡng đặt chỗ chờ R&D xác nhận), code so ngưỡng; AI soạn biên bản (bảng tiêu chí của code, ghi chú có dẫn chứng) và phiếu yêu cầu chỉnh sửa (mỗi tiêu chí trượt một mục, yêu cầu do AI viết, câu có số không có trong số đo bị bỏ); ở vòng sau kiểm từng mục phiếu cũ. Ô kết luận là lựa chọn Đạt / Cần chỉnh sửa / Hủy, để trống, gợi ý của code bên cạnh; duyệt Đạt sang BGĐ với biên bản, Cần chỉnh sửa gửi phiếu (lý do là nhận xét), Hủy đóng hồ sơ; phiếu đã duyệt có thư gửi NCC AI soạn. Tờ trình BGĐ (`10`, xong 9/10/2026): trước khi lane đối soát trình BGĐ duyệt mẫu đã đạt, AI soạn tờ trình (một lần mỗi vòng): code điền mã, tên, nhóm, NCC, kết quả đánh giá theo biên bản đã duyệt, đơn giá, tiền tệ, MOQ có trích dẫn từ báo giá; mô hình viết tóm tắt, rủi ro, đề xuất, mỗi câu dẫn hồ sơ, dòng lịch sử, biên bản hay báo giá đã đọc (mô hình không thấy giá); câu dẫn chứng từ hồ sơ khác hay có số không có trong bằng chứng bị bỏ. Trang duyệt của BGĐ hiện tờ trình như người xem được đọc (giá ẩn khi không có quyền xem dữ liệu thương mại); không có tờ trình thì trang ghi "chưa có tờ trình" và vẫn duyệt được; tin Zalo vẫn không mang mã và không giá. Bật theo policy chuẩn bị bước của tenant (`bod_submission`; Elmich bật). BM04 điền sẵn (`11`, xong 9/10/2026): khi hồ sơ vào bước 7, AI soạn BM04 từ hồ sơ, báo giá/spec và BM04 NCC gửi, biên bản đánh giá đã duyệt; mỗi ô nêu chứng từ và trích dẫn; giá, tiền tệ, MOQ, thời gian sản xuất, Incoterm do code chép từ chứng từ (mô hình không thấy giá); hai nguồn ghi khác nhau là mâu thuẫn, ô để trống, phát hiện nêu cả hai nguồn (giá chỉ nêu tên chứng từ); ô bắt buộc của biểu mẫu công ty không có nguồn được nêu; duyệt thì BM04 thành chứng từ và một phiên bản hồ sơ sản phẩm (giá chỉ ghi khi người duyệt có quyền ghi giá). Chốt với NCC (`12`, xong 9/10/2026): khi hồ sơ vào bước 8, thư chốt NCC được soạn từ BM04 đã duyệt (không giá) và đính kèm BM04; thư NCC trả lời, khi tải lên, được đọc (giá, tiền tệ, MOQ, thời gian sản xuất, quy cách, bao bì) và code so từng điều khoản với BM04: khớp, khác, thư chưa nêu, BM04 chưa có; câu "đồng ý mọi điều khoản" không thay giá đã đọc; các khác biệt nằm trong đề xuất TP Cung ứng duyệt, giá không bao giờ in số (trang duyệt và Zalo).

## 5. Chỗ nguồn tự mâu thuẫn

Ghi lại để hỏi, không tự chọn. Mỗi chỗ có số câu hỏi trong "Decisions owed".

1. **SLA BM04:** bảng tổng quan ghi "02 ngày làm BM04", bảng 17 bước ghi "SLA 4 ngày"
   ở bước 7 (QE-05).
2. **SLA tạo mã hàng:** bảng tổng quan ghi "0,5 ngày" cho bước 4 tổng quan; bảng 17
   bước không ghi SLA cho bước 9, nơi tạo mã hàng (QE-05).
3. **Ai duyệt PO:** bảng tổng quan để Ban Giám đốc "phê duyệt PO" ở bước 5; bảng 17
   bước để BGĐ và Kế toán ký ở bước 9 (trước PO), còn bước 10 chỉ bàn giao PO cho Kế
   toán (QE-06).
4. **Vòng chỉnh sửa mẫu:** bước 3 ghi "quay lại bước 2"; bước 4 cho R&D lập phiếu
   gửi qua Cung ứng tới NCC; bước 5 ghi "lặp lại bước 3" (QE-07).
5. **Bước 6 vắng khỏi sơ đồ:** dòng sơ đồ 3.1 đi "1 → 2 → 3 → 7", bỏ qua bước 6
   (BGĐ duyệt mẫu), trong khi bảng 17 bước có bước 6 (QE-03).
6. **Đánh số:** bảng tổng quan có 6 bước, bảng chi tiết 17 bước, hai cách đánh số
   không ánh xạ một-một. Repo dùng số của bảng 17 bước ở mọi nơi.

## 6. Điểm mở

Năm dấu **[Cần xác nhận]** của chính Elmich, giữ nguyên ý:

| Mã    | Chỗ trong nguồn         | Điểm cần xác nhận                                                         |
| ----- | ----------------------- | ------------------------------------------------------------------------- |
| QE-01 | Mục 1, bước 5 tổng quan | Thời gian duyệt chứng từ các cấp và thanh toán đặt cọc chưa có quy định   |
| QE-02 | Mục 2                   | Bộ phận lập và bộ phận nhận của từng tài liệu được suy luận, cần xác nhận |
| QE-03 | Mục 3.1                 | Sơ đồ chi tiết và sơ đồ con bước 12 được vẽ lại, cần Cung ứng xác nhận    |
| QE-04 | Mục 3.2, đoạn dưới bảng | Các con số SLA đã áp dụng thực tế hay còn là đề xuất                      |

Các dấu [Cần xác nhận] của Phần B không được chép, vì Phần B nằm ngoài phạm vi.
Câu hỏi do repo đặt thêm (QE-05 trở đi) và trạng thái của mọi câu nằm ở
`.claude/plans/supply-chain.md`, mục "Decisions owed".
