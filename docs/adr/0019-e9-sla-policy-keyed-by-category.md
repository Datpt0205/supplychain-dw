---
status: Accepted
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # đoạn dưới bảng: SLA theo Cate
    - ../../configs/policies/supply_chain_sla@2.0.0.yaml
    - ../../packages/python/dw_supply_chain/src/dw_supply_chain/domain/sla_evaluation.py # bản đồ mốc → trạng thái
---

# E9. Chính sách SLA có chiều Category; số của Elmich là override của tenant

Hôm nay `supply_chain_sla@1.2.0.yaml` là một tài liệu cho mỗi tenant, không có
Category; `sla_evaluation.py` gắn bốn mốc vào trạng thái và đo từ lúc vào trạng thái
đó. Hai mốc `bm04` và `supplier_confirmation` có trong file nhưng không trạng thái
nào đọc (failure-modes #1). Elmich nói các bước không có số cụ thể "áp dụng SLA theo
Cate".

**Quyết định:**

- Phiên bản schema mới `supply_chain_sla@2.0.0`: `default` (như hôm nay) cộng
  `by_category: {<category>: {<milestone>: ...}}`. Thiếu mốc ở Category thì dùng
  `default`. Danh sách Category là dữ liệu của tenant trong policy, không phải enum
  trong code.
- Category được chọn ở bước 1, đóng dấu lên Hồ sơ phát triển và chép sang Hồ sơ PO
  lúc ĐẶT HÀNG. Bộ đánh giá nhận Category của hồ sơ.
- Mốc mới gắn trạng thái: `bm04` → `profile_in_progress`, `supplier_confirmation` →
  `supplier_confirmation`, cùng các mốc giai đoạn 1 khác (lấy mẫu, test mẫu) mà
  Category nào có số thì đo.
- Số của Elmich (BM04 4 ngày, chốt SP 5, đặt cọc 10, về cảng 21, thanh toán 10, nhập
  kho 5) vào **override của tenant Elmich**, đánh dấu `pending_business_confirmation`
  cho tới khi Elmich trả lời QE-04 và QE-05. Mặc định của nền tảng giữ số thử 1–2
  ngày.
- Follow-up quá hạn có thêm loại người nhận `pic`: PIC của hồ sơ cùng các scope đã
  đóng dấu, vẫn đóng dấu lên follow-up lúc mở.

## Phương án đã cân nhắc

- **Một policy cho mỗi Category.** Bác: nhân bản phần chung, và một mốc thêm sau phải
  sửa mọi file.
- **Category là enum trong code.** Bác: Category của Elmich không phải của khách sau
  (nền tảng phải co giãn theo tenant).

## Hệ quả

- `duration_days_for()` vẫn coi mốc `pending` là vắng, nên số chưa xác nhận không bật
  cảnh báo nào. Đây là chủ ý: cảnh báo trên số chưa chốt dạy người dùng bỏ qua cảnh báo.
- Đồng hồ bắt đầu từ đâu và có dừng khi hồ sơ bị chặn hay không vẫn theo cách hôm nay
  (từ lúc vào trạng thái; `not_applicable` khi bị ngắt) cho tới khi Elmich trả lời
  QE-14.
- Policy đọc lúc đánh giá, không đóng dấu lên hồ sơ: đổi số SLA thì hồ sơ đang chạy
  đo theo số mới. Category thì đóng dấu.

## Sửa đổi 2026-10-07 (tạm, lát S6; Đạt ủy quyền quyết các điểm mở)

Các quyết định tạm của lead khi làm lát S6 (ticket 06 giai đoạn 1). Chi tiết, test và
mutation ở Comments của
`.claude/plans/supply-chain/stage-1/issues/06-sla-by-category-and-pic-routing.md`.

1. **Schema 2.0.** `categories` (khóa, nhãn; ít nhất một; khóa dạng slug
   `^[a-z0-9][a-z0-9_-]{0,63}$`, không trùng), `default` (trước là `sla`), `by_category`
   (khóa phải có trong `categories`, gõ sai bị từ chối). Mốc riêng của Category thắng
   nguyên khối, kể cả trạng thái: mốc riêng còn `pending` thì không đánh giá, không rơi
   về số của `default`. Hồ sơ đóng dấu khóa, trang đọc nhãn, nên đổi nhãn không chạm hồ sơ.
2. **Category kiểm một chỗ** (`require_category` trong `handlers.py`): `propose` (web và
   Zalo) và `CreatePOCase` (Category tùy chọn) chỉ nhận đúng một khóa của danh sách tenant,
   sau kiểm quyền; khác thì 422 nêu `category`. Hồ sơ mở trước S6 giữ chữ tự do đã đóng
   dấu (không có trong danh sách), đánh giá theo `default`, hiển thị nguyên chữ. Không ai
   đổi Category của hồ sơ đã mở.
3. **Chat Zalo giải Category trên danh sách tenant** (thay quyết định A2 của Z4b): chữ
   người gửi (đã kiểm nguyên văn) khớp khóa hoặc nhãn, bỏ hoa thường và dấu, đúng một mục
   thì giữ khóa; không khớp hoặc khớp nhiều thì không ghi, hỏi lại với tối đa 5 lựa chọn
   của chính tenant. Mô hình không thấy danh sách; prompt không đổi. "Đồng ý" trên bản nháp
   mà Category đã rời danh sách thì không tạo hồ sơ, bỏ Category và hỏi lại.
4. **Override cũ:** migration `d56da3dd2146` nâng mọi override 1.x lên 2.0: `sla` thành
   `default` giữ nguyên mọi số, thêm `categories` bằng danh sách nền (`noi`, `chao`),
   không có `by_category`. Không tenant nào đổi số vì deploy.
5. **Số (QE-01, QE-04, QE-05, QE-13).** Nền 2.0.0 giữ số thử ngắn (1–2 ngày, mọi mốc
   `confirmed`) và hai Category thử `noi`, `chao` (chảo test mẫu 1 ngày, ví dụ `by_category`).
   Mốc mới gắn trạng thái hồ sơ phát triển: `sample_collection`, `sample_testing`,
   `bod_review` (QE-01), `bm04`, `supplier_confirmation`, `item_coding` (Elmich nêu 0,5
   ngày, đơn vị nhỏ nhất là ngày, QE-05), `signoff` (QE-01). Số của Elmich là override
   của tenant, ghi bằng `scripts/seed_supply_chain_demo.py elmich-sla` qua handler của
   `PUT /sla-policy` (cùng schema, quyền, audit; không cần API đang chạy), từ
   `scripts/elmich_sla_override.yaml`: BM04 4, chốt SP 5, đặt cọc 10, về cảng 21, thanh
   toán 10, nhập kho 5, mọi mốc `pending_business_confirmation`; Category tạm Nồi, Chảo,
   Khác; nhắc NCC 5, leo thang 10 ngày.
6. **Đồng hồ (QE-14, tạm), như Hồ sơ PO từ trước:** đo từ lần chuyển gần nhất vào trạng
   thái đang ở; đang chờ bên ngoài, bị chặn, xem xét tay thì không mốc nào áp dụng; tiếp
   tục thì đồng hồ tính lại từ lúc tiếp tục. Policy đọc lúc đánh giá.
7. **Follow-up của hồ sơ phát triển.** `follow_ups` thêm `product_dev_case_id` (FK
   `(tenant, workspace, id)`, CASCADE) và `ck_follow_ups_one_case`; chỉ vi phạm SLA, vì
   giai đoạn 1 không có cập nhật NCC. Bảng nay hẹp theo workspace bằng RLS (hình chuẩn của
   `CLAUDE.md`), cả với follow-up của Hồ sơ PO: thông báo vốn chỉ tới thành viên workspace
   của hồ sơ, nay danh sách và nút đóng cũng vậy. Sweep chạy theo từng workspace
   (`supply_chain.workspaces_with_cases()` thay `tenants_with_cases()`).
8. **Người nhận `pic`** (`supply_chain_follow_ups@1.1.0`, schema 1.1): một mục trong danh
   sách người nhận của loại, cạnh scope; loại nào gọi `pic` vẫn phải có ít nhất một scope
   (PIC vắng, đã nghỉ, hồ sơ cũ không có PIC thì scope vẫn nhận, QE-18 tạm). PIC đóng dấu
   lúc mở (`recipient_user_id`) chỉ khi còn membership workspace của hồ sơ; lúc gửi hỏi
   lại membership; lane kênh hỏi lần nữa trước khi gửi Zalo. PIC đóng dấu được xem việc
   là của mình và đóng được. Nền 1.1.0: mọi loại gọi `pic`; leo thang và trễ SLA thêm
   `supply_chain.duty.supply_lead` (TP Cung ứng). Override 1.0 vẫn hợp lệ, không được nâng:
   thêm PIC vào cách chia việc tenant đã tự đặt là đổi việc của họ.
9. **Đổi PIC** (`ReassignProductCasePic`, `ReassignPOCasePic`): lệnh riêng, không phải
   bước của máy trạng thái (không dòng lịch sử, không mục trong policy duty); duty cố định
   `supply_lead` (ai được đổi PIC còn là câu QE-18); bắt buộc lý do; người mới phải là
   thành viên workspace; audit cùng giao dịch, lạc quan theo version. Hồ sơ phát triển
   `ordered` hoặc `cancelled` từ chối (409); Hồ sơ PO `completed` hoặc `cancelled` cũng
   vậy. Follow-up đã mở giữ người đã đóng dấu.
10. **Tiêu đề thông báo chỉ nêu định danh** (QE-20): số PO hoặc mã đề xuất, không tên SP,
    không NCC; thân tin trong ứng dụng giữ NCC như trước.
11. **Chưa làm ở S6:** báo cáo hằng ngày cho TP Cung ứng (QE-19, không thuộc ticket; làm
    tạm ở S8, ADR 0016 sửa đổi S8); giao diện đổi PIC (API có, trang chưa có nút).

## Sửa đổi 2026-10-08 (HR3, Đạt giao quyết tạm): đồng hồ bước 17

`warehouse_receipt` đo **từ lúc thanh toán xong tới lúc hàng vào kho** (QE-14, tạm), không
còn từ thanh toán tới lúc *bắt đầu* nhập: mốc gắn cả `payment_completed` và
`warehouse_receiving`; đồng hồ của `warehouse_receiving` bắt đầu ở lần vào
`payment_completed` gần nhất (`domain/sla_evaluation.py`, `SLA_CLOCK_STARTS_IN` và
`sla_clock_start_state`, một chủ; SQL của repository dựng từ bảng đó); `completed` là
trạng thái kết thúc nên đồng hồ dừng. Tạm dừng trong lúc nhập kho rồi tiếp tục về
`warehouse_receiving` không khởi động lại đồng hồ (đồng hồ là của khoảng từ thanh toán);
tạm dừng lúc `payment_completed` rồi tiếp tục thì vẫn tính lại như mục 6. Port đổi tên
`get_current_state_entered_at` / `bulk_current_state_entered_at` thành
`get_sla_clock_started_at` / `bulk_sla_clock_started_at`. Mô tả chữ của mốc trong
`supply_chain_sla@2.0.0.yaml` ("PAYMENT_COMPLETED → WAREHOUSE_RECEIVING") đã cũ nhưng không
sửa tại chỗ: file đã phát hành, checksum ghim trong release manifest; bản policy kế tiếp sửa.

