# Supply Chain (Elmich)

> Mục ghi "(đề xuất)" là chữ glossary này chọn cho một khái niệm chưa có code hoặc
> chưa có nhãn trên giao diện, chờ Đạt duyệt; "(từ S1)", "(từ D)"... nói ticket nào đưa
> nó vào code (`.claude/plans/supply-chain.md`, bảng Slices). Mục không ghi gì đã có
> trong code. Mục ghi "(còn mở)" trỏ tới câu hỏi QE
> trong `.claude/plans/supply-chain.md`. Nhãn trạng thái Hồ sơ PO trong code có một
> chủ: `CASE_STATE_LABEL` trong `apps/web/components/supply-chain/case-state-badge.tsx`;
> bảng dưới chép lại để đọc, và khi hai bên lệch thì sửa cả hai trong cùng commit.

Context `dw_supply_chain` theo dõi quy trình cung ứng của Elmich, Phần A, 17 bước
(`docs/products/elmich/process.md`): từ lúc Cung ứng đề xuất một sản phẩm mới, qua lấy
mẫu, duyệt, Profile, mã hàng, tới ĐẶT HÀNG, rồi theo dõi PO tới khi hàng nhập kho. Mô
hình đọc câu chữ; code quyết trạng thái, quyền và SLA; người quyết mọi bước có hậu quả.

## Language

### Hồ sơ và giai đoạn

**Giai đoạn 1** (Phát triển sản phẩm):
Bước 1–9: chọn sản phẩm, lấy mẫu và duyệt mẫu, BM04, thống nhất với NCC, mã hàng và
SKU, trình ký. Kết thúc ở nút ĐẶT HÀNG.
_Avoid_: "luồng 1–4" (đánh số của bảng tổng quan trong PDF).

**Giai đoạn 2** (Theo dõi cung ứng):
Bước 10–17: tạo PO, đặt cọc, thiết kế màu và bao bì, sản xuất, QC và đóng cont, hàng về
cảng, thanh toán, nhập kho.

**Bước** (1–17):
Số bước theo bảng 17 bước của process.md mục 3.2. Repo dùng số này ở mọi nơi.
_Avoid_: số bước của bảng tổng quan 6 bước.

**Hồ sơ phát triển sản phẩm** (`ProductDevelopmentCase`) (đề xuất):
Một sản phẩm được đề xuất ở bước 1 và đi qua giai đoạn 1 trong một workspace. Mang mã
đề xuất, tên sản phẩm, Category, NCC, PIC, vòng mẫu, chứng từ và trạng thái. Danh sách
SP đề xuất của bước 1 là nhiều hồ sơ, mỗi hồ sơ một sản phẩm.
_Avoid_: "case" trơn; "đơn" (đơn là PO).

**Hồ sơ PO** (`POCase`):
Một đơn đặt hàng với NCC, từ lúc tạo PO tới khi nhập kho xong hoặc hủy. Hôm nay mang số
PO, NCC, trạng thái, lịch sử chuyển trạng thái, cập nhật của NCC và phân tích ảnh hưởng
trễ. Loại đơn, PIC, Category, các dòng PO và trạng thái Chờ tạo PO đến từ S5.
_Avoid_: "PO" trơn khi muốn nói hồ sơ (PO là chứng từ, Hồ sơ PO là thứ hệ thống theo
dõi); "đơn hàng" trong code.

**Vòng mẫu** (`SampleRound`) (đề xuất):
Một lần nhận mẫu từ NCC và R&D test nó. Vòng 1 bắt đầu ở bước 2; mỗi lần mẫu chỉnh
sửa về (bước 5) mở vòng mới. Kết quả: Đạt, Cần chỉnh sửa, Hủy. Hủy hồ sơ khi mẫu đang
test cũng đóng vòng đó với kết quả Hủy.

**Phiếu yêu cầu chỉnh sửa mẫu** (`SampleRevisionRequest`) (từ S1):
Chứng từ R&D lập ở bước 4 khi mẫu không đạt, gửi qua Cung ứng tới NCC. Bắt buộc để
chuyển sang chờ mẫu chỉnh sửa.

**Biên bản đánh giá mẫu** (chứng từ `doc_type = sample_evaluation`) (từ S1, D):
Kết quả test kỹ thuật và chất lượng của một vòng mẫu, do R&D lập. Bắt buộc khi mẫu Đạt.

**Profile SP (BM04)**:
Biểu mẫu 04, hồ sơ kỹ thuật sản phẩm đã chốt (thông số, hình ảnh, giá), do R&D và
Cung ứng lập ở bước 7. Có mốc SLA `bm04`.
_Avoid_: "profile" trơn (Phần B dùng "Profile" cho nội dung marketing, ngoài phạm vi).

**Mã hàng** (Mã hàng hóa chính thức, `ItemCode`) (từ S4):
Mã định danh sản phẩm dùng để đặt hàng, Cung ứng tạo ở bước 9. Không trùng trong một
tenant; database giữ điều đó (ADR 0018). Một hồ sơ phát triển có một mã hàng.
_Avoid_: "mã hàng mẫu" (là mã đề xuất ở bước 1), "mã SP".

**SKU** (`Sku`) (từ S4):
Một biến thể bán được của một mã hàng. Chỉ thêm sau khi có mã hàng chính thức; không
trùng trong một tenant. "Chốt số lượng SKU" của bước 9 còn mở (QE-11).

**Mã đề xuất** (`proposal_code`) (đề xuất):
Mã Cung ứng đặt cho sản phẩm ở bước 1 ("xác định mã hàng mẫu"). Không trùng trong một
tenant. Không phải mã hàng.

**ĐẶT HÀNG** (bàn giao, `PlaceOrder`) (từ S5):
Nút cuối giai đoạn 1. Trong một giao dịch: hồ sơ phát triển sang Đã đặt hàng, và một Hồ
sơ PO mới ở trạng thái Chờ tạo PO mang PIC, Category, NCC và các SKU (ADR 0017). Bấm hai
lần không tạo hai PO.
_Avoid_: "đặt hàng" viết thường để chỉ bước 10 (bước 10 là Tạo PO).

**Loại đơn** (`order_kind`) (từ S5):
Hàng mới (`new`, sinh từ ĐẶT HÀNG) hoặc Hàng đặt lại (`reorder`, tạo thẳng không qua
giai đoạn 1). Đặt ở bước 10.

**Dòng PO** (`po_case_lines`) (đề xuất, từ S5):
Một SKU và số lượng trong Hồ sơ PO.

**Category** (ngành hàng) (từ S1, S6):
Nhóm sản phẩm của tenant, chọn ở bước 1, đóng dấu lên hồ sơ và chép sang Hồ sơ PO. SLA
của các bước không có số cụ thể đọc theo Category (ADR 0019). Danh sách là dữ liệu của
tenant (còn mở, QE-13).
_Avoid_: "cate", "danh mục" (danh mục là danh sách tài liệu ở process.md mục 2).

### Người và quyền

**PIC** (Người phụ trách, `pic_user_id`):
Người tạo hồ sơ ở bước 1, đóng dấu lúc tạo và theo hồ sơ suốt 17 bước, chép sang Hồ sơ
PO lúc ĐẶT HÀNG. Không bao giờ suy lại; đổi bằng hành động Đổi PIC có lý do và audit.
Thay cách phân PIC theo Category trước đây.
_Avoid_: "owner", "người tạo" khi muốn nói PIC sau khi đã đổi.

**NCC** (Nhà cung cấp):
Bên làm mẫu và sản xuất. Hôm nay nhận diện theo tên đúng như đã lưu; chưa có danh mục
NCC.
_Avoid_: "supplier" trong chữ tiếng Việt trên giao diện.

**Duty** (nhiệm vụ, `supply_chain.duty.<duty>`):
Quyền làm một nhóm hành động trên hồ sơ. Có trong code (`CaseDuty`): `ordering`
(Cung ứng), `finance` (Kế toán), `qc`, `logistics`, `warehouse`, `exceptions`, và ở
giai đoạn 1 `rnd` (R&D, từ S1), `supply_lead` (TP Cung ứng, từ S3). Hành động nào
thuộc duty nào là policy tenant ghi đè được.
_Avoid_: "vai" (vai là `sc_*`, gom scope và duty).

**Vai** (`sc_*`):
`sc_viewer`, `sc_operator`, `sc_finance`, `sc_qc`, `sc_logistics`, `sc_warehouse`,
`sc_process_admin`, `sc_rnd` (S1), `sc_bod` (S2), `sc_supply_lead` (S3); danh mục còn
mở (QE-16). Không có `sc_accounting`: xem **Kế toán**.

**Kế toán**:
Một vai, `sc_finance` (duty `finance`): xác nhận đặt cọc ở bước 11 và thanh toán ở
bước 16, nhận báo ở bước 10, và từ S4 ký ở bước 9 qua scope
`supply_chain.approve.accounting` cấp thêm cho `sc_finance`. Chỉ tách thành vai riêng
(`sc_accounting`) nếu Elmich trả lời QE-16 rằng người ký và người xác nhận tiền là hai
người.
_Avoid_: "kế toán duyệt" và "kế toán xác nhận cọc" như hai vai khi chưa có QE-16.

**BGĐ** (Ban Giám đốc):
Duyệt mẫu ở bước 6 và ký ở bước 9, qua approval có `required_scope`
`supply_chain.approve.bod`. Không có duty.

**TP Cung ứng** (Trưởng phòng Cung ứng):
Xác nhận SP đã thống nhất với NCC ở bước 8 (duty `supply_lead`), nhận báo cáo mẫu hằng
ngày ở bước 3, đổi PIC.

**Người quyết** (`required_scope`) (từ A):
Scope đóng dấu lên một approval lúc tạo; chỉ người giữ nó (cùng `approvals.decide`)
quyết được (ADR 0020).

**Tách nhiệm** (SoD):
Một người không giữ cả hai phía của một cặp nhiệm vụ xung đột; database từ chối
membership vi phạm. Năm luật của context được miễn trừ có lý do và audit.

### Diễn tiến

**Hành động** (`CaseAction`, `ProductAction`):
Một bước chuyển có tên, canh điều kiện trong aggregate. Một số bắt buộc lý do.

**Approval** (duyệt):
Một câu hỏi cho người, dừng run có checkpoint tới khi được quyết, trên web hoặc trong
Zalo. Trong Zalo chỉ bằng mã dùng một lần hiện trên cổng khi người duyệt mở đúng phiên
bản hồ sơ (`DUYỆT <mã>`, `TỪ CHỐI <mã> <lý do>`), không bao giờ bằng chữ tự do; tin Zalo
không mang mã (ADR 0014, Z5). Tiền tố `supply_chain.case_action.` và
`supply_chain.product_action.` là nghiêm: người yêu cầu không tự duyệt, phải có nhận xét
(nhận xét trong lệnh Zalo còn chờ QO-7).
_Avoid_: "phê duyệt" trong code; "duyệt trong Zalo" để chỉ trả lời "ok", "duyệt" không
kèm mã (không được, ADR 0014).

**Trình ký** (`signoff`) (từ S4):
Chuỗi approval ở bước 9, mỗi bước một người quyết, thứ tự theo policy
`supply_chain_product_approvals` (còn mở, QE-10).

**Chứng từ** (`CaseDocument`) (từ D):
Một file gắn vào hồ sơ, có loại (`doc_type`) và phiên bản, lưu trong object storage
dưới tiền tố tenant và workspace (ADR 0021).
_Avoid_: "evidence" khi nói chứng từ (evidence là trích dẫn nguyên văn của cập nhật
NCC).

**Mốc SLA** (milestone):
Thời hạn của một trạng thái, đo từ lúc hồ sơ vào trạng thái đó. Có trong policy
`supply_chain_sla@1.2.0`: `deposit`, `port_arrival`, `payment`, `warehouse_receipt`
(đọc bởi `sla_evaluation.py`) và `bm04`, `supplier_confirmation` (có trong policy,
chưa trạng thái nào đọc, gắn ở S6). Thêm ở S6: `sample_collection` (`sample_requested`)
và `sample_testing` (`sample_testing`). Mốc chưa xác nhận không bật cảnh báo.
Cùng policy có khối `supplier_update` (nhắc sau 1d, leo thang sau 2d NCC im lặng): đó
là nhịp cập nhật NCC, không phải mốc SLA.

**Follow-up**:
Một việc nhắc do worker mở khi đến hạn nhắc, cần leo thang hoặc vi phạm SLA; đóng khi
tín hiệu hết. Người nhận đóng dấu lúc mở (theo scope, và PIC).

**Cập nhật NCC** (`SupplierUpdate`):
Tin của NCC mà mô hình đọc thành loại sự kiện, kèm trích dẫn nguyên văn; tin thiếu trích
dẫn hoặc độ tin thấp chờ người xác nhận.

**Daily brief**:
Bản tóm tắt hằng ngày theo nhóm tín hiệu, thứ tự tenant đặt; câu tóm tắt AI chỉ giữ khi
mọi con số và tên thuộc nhóm nó dẫn.

**Liên kết kênh** (channel link) (từ Z1):
Chat id Zalo của một người dùng, do chính họ liên kết bằng token dùng một lần. Không phải
danh tính đăng nhập (ADR 0012).

**Lần gửi kênh** (channel delivery) (từ Z2):
Một lần gửi một thông báo tới một người qua một kênh, có trạng thái, thử lại và audit
(ADR 0013).

### Trạng thái của Hồ sơ PO (`CaseState`)

| Giá trị               | Nhãn                 | Ghi chú                                     |
| --------------------- | -------------------- | ------------------------------------------- |
| `order_requested`     | Chờ tạo PO           | (đề xuất) Mới, ticket S5; chưa có số PO     |
| `po_created`          | Đã tạo PO            | Bước 10                                     |
| `waiting_deposit`     | Chờ đặt cọc          | Bước 11; mốc `deposit`                      |
| `deposit_confirmed`   | Đã xác nhận cọc      | Bước 11                                     |
| `pre_production`      | Chuẩn bị sản xuất    | Bước 12, một trạng thái thô                 |
| `production`          | Đang sản xuất        | Bước 13                                     |
| `qc`                  | Kiểm tra chất lượng  | Bước 14                                     |
| `in_transit`          | Đang vận chuyển      | Sau QC đạt; mốc `port_arrival`              |
| `arrived_port`        | Đã đến cảng          | Bước 15                                     |
| `waiting_payment`     | Chờ thanh toán       | Bước 16; mốc `payment`                      |
| `payment_completed`   | Đã thanh toán        | Bước 16; mốc `warehouse_receipt`            |
| `warehouse_receiving` | Đang nhập kho        | Bước 17                                     |
| `completed`           | Hoàn tất             | Kết thúc                                    |
| `waiting_external`    | Chờ bên ngoài        | Ngắt; `resume` về trạng thái trước          |
| `blocked`             | Đang bị chặn         | Ngắt                                        |
| `rework`              | Làm lại              | Chỉ vào từ QC không đạt, ra về `production` |
| `manual_review`       | Cần xem xét thủ công | Ngắt                                        |
| `cancelled`           | Đã hủy               | Kết thúc                                    |

### Trạng thái của Hồ sơ phát triển sản phẩm (`ProductDevState`) (đề xuất)

Từ S1, nhãn trong code có một chủ: `PRODUCT_DEV_STATE_LABEL` trong
`apps/web/components/supply-chain/product-case-labels.tsx`; S2 đi tới
`profile_in_progress` (cùng ba trạng thái ngắt và `cancelled`), S3 tới `item_coding`, S4
tới `ready_to_order`.
Hai bên lệch thì sửa cả hai trong cùng commit. Ở `pending_bod_review` người dùng chỉ hủy
được; `bod_approve` và `bod_reject` do graph duyệt áp sau quyết định ở `/approvals`.
`complete_profile` (R&D) cần Profile SP (BM04), `confirm_with_supplier` (TP Cung ứng)
cần email xác nhận của NCC, mỗi file của chính hồ sơ và tải lên từ khi hồ sơ tới bước đó.
Ở `item_coding` Cung ứng cấp (hoặc sửa) mã hàng, thêm và bỏ SKU, rồi trình ký; ở
`pending_signoff` người dùng chỉ hủy được, `signoff_approve` và `signoff_reject` do graph
trình ký áp sau từng bước ký ở `/approvals` (thứ tự ký là policy của tenant).

| Giá trị                 | Nhãn                   | Bước, ghi chú                                   |
| ----------------------- | ---------------------- | ----------------------------------------------- |
| `proposed`              | Đề xuất                | Bước 1; PIC đóng dấu                            |
| `sample_requested`      | Đang lấy mẫu           | Bước 2                                          |
| `sample_testing`        | Đang test mẫu          | Bước 3, 5                                       |
| `revision_requested`    | Chờ mẫu chỉnh sửa      | Bước 4; có Phiếu yêu cầu chỉnh sửa              |
| `pending_bod_review`    | Chờ BGĐ duyệt          | Bước 6                                          |
| `profile_in_progress`   | Đang làm BM04          | Bước 7; mốc `bm04`                              |
| `supplier_confirmation` | Chờ thống nhất với NCC | Bước 8; mốc `supplier_confirmation`             |
| `item_coding`           | Đang tạo mã hàng       | Bước 9                                          |
| `pending_signoff`       | Chờ trình ký           | Bước 9                                          |
| `ready_to_order`        | Sẵn sàng đặt hàng      | Bước 9, chờ ĐẶT HÀNG                            |
| `ordered`               | Đã đặt hàng            | Kết thúc; Hồ sơ PO đã tạo                       |
| `waiting_external`      | Chờ bên ngoài          | Ngắt                                            |
| `blocked`               | Đang bị chặn           | Ngắt                                            |
| `manual_review`         | Cần xem xét thủ công   | Ngắt                                            |
| `cancelled`             | Đã hủy                 | Kết thúc; mẫu Hủy ở bước 3 hoặc BGĐ không duyệt |

## Relationships

- Một **Hồ sơ phát triển sản phẩm** có nhiều **Vòng mẫu**, mỗi vòng không đạt có một
  **Phiếu yêu cầu chỉnh sửa mẫu**; có tối đa một **Mã hàng**, mã hàng có một hoặc nhiều
  **SKU**.
- **ĐẶT HÀNG** tạo một **Hồ sơ PO** từ một hồ sơ phát triển; một sản phẩm sinh mấy PO
  còn mở (QE-12). Hồ sơ PO Hàng đặt lại không có hồ sơ phát triển.
- **PIC**, **Category** đóng dấu ở hồ sơ phát triển và chép sang Hồ sơ PO.
- **Chứng từ** thuộc đúng một hồ sơ (phát triển hoặc PO).
- **Follow-up** thuộc một hồ sơ; người nhận đóng dấu lúc mở.

## Flagged ambiguities

- "PO" vừa là chứng từ đặt hàng vừa là cách gọi tắt Hồ sơ PO: trong code và ticket
  dùng **Hồ sơ PO** khi nói thứ hệ thống theo dõi.
- "Profile" ở Phần A là BM04; ở Phần B là nội dung marketing (ngoài phạm vi).
- "Đặt hàng" vừa là nút ĐẶT HÀNG (cuối bước 9) vừa là "Đặt hàng & phê duyệt" của bảng
  tổng quan (bước 5 tổng quan, gồm tạo PO và đặt cọc): viết **ĐẶT HÀNG** cho nút, **Tạo
  PO** cho bước 10.
- "Mã hàng mẫu" ở bước 1 là **Mã đề xuất**, không phải **Mã hàng**.
