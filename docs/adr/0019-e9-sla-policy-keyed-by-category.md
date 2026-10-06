---
status: Accepted
date: 2026-10-05
source:
    - ../products/elmich/process.md#32-bảng-17-bước # đoạn dưới bảng: SLA theo Cate
    - ../../configs/policies/supply_chain_sla@1.2.0.yaml
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
