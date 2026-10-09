---
status: Accepted (Đạt, 2026-10-09; mặc định của lead)
date: 2026-10-09
source:
    - ../../../../../docs/products/elmich/process.md#31-sơ-đồ-chi-tiết # sơ đồ con bước 12
    - ../../../../../.claude/plans/supply-chain/packaging-design/spec.md
---

# E17. MKT là người dùng tối thiểu ở bước 12

Sơ đồ con bước 12 có MKT (nhận BM04, HDSD, maquette; lên nội dung bao bì) và Thiết kế. Lát
PK ghi "báo TP MKT" thành một dòng lịch sử và để phần MKT "chờ Phần B"; nhưng Phần B ngoài
phạm vi (Đạt, 5/10/2026), nên câu đó là chờ mãi.

**Quyết định:**

1. Vai `sc_mkt` = `sc_viewer` + duty `mkt` + tải chứng từ loại `packaging_content`,
   `user_manual`, `maquette`, `packaging_design`. Không giá (E15).
2. Bước con mới trên `packaging_designs`, giữa duyệt màu và duyệt thiết kế:
   `send_mkt_pack` (`ordering`; AI chuẩn bị gói và đề xuất, E14) → `submit_packaging_content`
   (`mkt`, cần `packaging_content`). "Báo TP MKT" thành thông báo cho người giữ duty `mkt`.
3. Thiết kế (người làm file bao bì) tải `packaging_design` bằng vai `sc_mkt`; không vai
   riêng tới khi Elmich cần.
4. Sản xuất nội dung marketing (Profile, media, content của Phần B) vẫn ngoài phạm vi.

## Hệ quả

- Policy duty Hồ sơ PO phiên bản mới, override cũ lấy duty nền tảng cho bước thêm sau.
- Ticket `ai-automation/issues/16`.
