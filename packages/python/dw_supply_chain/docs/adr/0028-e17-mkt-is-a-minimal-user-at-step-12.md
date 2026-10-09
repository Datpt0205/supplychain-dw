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

## Sửa đổi 2026-10-10 (tạm, lát AI-16; lead quyết các điểm ADR để mở)

1. **Bật theo tenant.** Hai bước con của MKT chỉ có khi policy đóng gói của tenant đòi
   (`supply_chain_packaging@1.1.0`, `require_packaging_content`; nền tảng false, Elmich true). Ở đó
   duyệt màu không còn ghi "đã báo TP MKT": Cung ứng `send_mkt_pack` (ghi dòng "đã gửi MKT gói", báo
   người giữ duty của `submit_packaging_content`), MKT `submit_packaging_content` với một
   `packaging_content` tải lên từ khi gửi gói (nộp lại được tới khi thiết kế được duyệt; báo người
   giữ duty của `approve_design`); thiết kế chưa duyệt được trước khi nội dung được nộp. Tenant khác
   giữ luồng cũ, không thấy hai bước.
2. **Vai `sc_mkt`** = `sc_viewer` + `supply_chain.duty.mkt` + `supply_chain.packaging_document.write`
   (scope mới: chỉ tải bốn loại `packaging_content`, `user_manual`, `maquette`, `packaging_design`;
   handler quyết theo loại). Không `commercial.read`, không `duty.ordering`, không `document.write`;
   cả hai scope ở phía vận hành của `sod_sc_rules_vs_operations` (migration `4527f22c2031`).
3. **Policy duty Hồ sơ PO 1.3.0** thêm `send_mkt_pack: ordering`, `submit_packaging_content: mkt`.
   Override lưu trước 1.3.0 không bị viết lại: khi nạp, `SupplyChainActionDuties.from_stored` cho đúng
   hai bước đó duty nền tảng (mẫu `STEPS_ADDED_AFTER` của policy hồ sơ sản phẩm); thiếu bước khác vẫn
   bị từ chối.
4. **Gói MKT** là các chứng từ đã có (BM04 của hồ sơ sản phẩm, HDSD, maquette mới nhất), trang liệt
   kê có/chưa; lane `supply_chain_packaging_papers` soạn khung nội dung bao bì và HDSD từ BM04 khi màu
   được duyệt (bật bằng `packaging` của policy chuẩn bị bước). MKT xem, sửa bản nháp, tải file lên
   rồi nộp; bản nháp không tự thành chứng từ.
5. **Kiểm bản in**: `packaging_design` (PDF có lớp chữ) được lane trích xuất đọc
   (`extract_packaging_design@1.0.0`, khai skill `label_rules@1.1.0`); mã vạch do code đọc (dãy 13 số
   bị che như số tài khoản trước mô hình). Code so với luật nhãn (`LABEL_REQUIRED`, test giữ mỗi mục
   là một dòng của skill), BM04 (tên, chất liệu, xuất xứ như chữ; kích thước như số), SKU của PO và số
   kiểm tra của mã vạch. Ảnh và PDF quét vẫn `unreadable` (chưa có OCR). Có điểm sai thì soạn yêu cầu
   sửa thiết kế (`design_revision_request`, mỗi điểm một mục, yêu cầu theo chữ của BM04); mẫu màu chưa
   duyệt có khung yêu cầu sửa màu (`colour_revision_request`, màu theo BM04, ô yêu cầu trống). Duyệt
   và gửi lại sửa vẫn là bước của Cung ứng.
