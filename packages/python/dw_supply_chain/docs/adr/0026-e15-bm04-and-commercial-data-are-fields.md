---
status: Accepted (Đạt, 2026-10-09)
date: 2026-10-09
source:
    - ../../../../../docs/products/elmich/process.md#2-danh-mục-tài-liệuchứng-từ-nghiệp-vụ-chính # BM04: thông số, hình ảnh, giá
    - ../../src/dw_supply_chain/domain/po_case.py # POCase, POCaseLine: không có giá
    - 0025-e14-ai-prepares-a-step-a-person-approves-the-move.md
---

# E15. BM04 và dữ liệu thương mại là trường có kiểu, sau một scope giá

BM04 hôm nay là một file tải lên; Hồ sơ PO không có giá, tiền tệ, điều khoản hay ngày
giao; đặt cọc và thanh toán không có số tiền. Không có gì có cấu trúc để AI soạn từ đó
(bước 8, 9, 10, 12) hay để code kiểm (PI, hóa đơn, đếm hàng).

**Quyết định:**

1. **BM04 là một biểu mẫu trong ứng dụng.** `supply_chain.product_profiles`: một phiên bản
   mỗi lần lưu (chỉ thêm), FK ghép tới hồ sơ phát triển, workspace RLS. Cột có kiểu cho
   những gì code tính với: đơn giá, tiền tệ, MOQ, thời gian sản xuất (ngày), Incoterm;
   `attributes jsonb` cho phần còn lại (thông số, biến thể, bao bì), kiểm theo schema của
   tenant (policy `supply_chain_bm04_schema@1.0.0`, `TenantOverlay`). `complete_profile`
   dựng file BM04 từ phiên bản đã duyệt qua mẫu (E14) thành chứng từ
   `product_profile_bm04`. Tải lên file BM04 vẫn được, như đường dự phòng.
2. **Dữ liệu thương mại của Hồ sơ PO:** tiền tệ, Incoterm, điều khoản thanh toán, % cọc,
   ngày giao dự kiến trên `po_cases`; đơn giá trên `po_case_lines`; tổng do code tính, không
   lưu hai lần. `supply_chain.po_payments` (cọc / thanh toán cuối: số tiền, hạn, ngày trả,
   tham chiếu chứng từ), chỉ thêm.
3. **NCC:** người liên hệ (tên, email) và tài khoản ngân hàng trên danh mục NCC, chỉ thêm
   phiên bản; tài khoản chỉ code đọc và so, không bao giờ vào prompt.
4. **Scope giá:** `supply_chain.commercial.read` / `.write`. Thiếu scope thì API trả trường
   giá rỗng có lý do, không phải số 0. **Không bao giờ qua Zalo:** tóm tắt duyệt, câu trả
   lời hỏi đáp và thông báo không mang giá (QE-20), có test.
5. Mọi bảng mới có `tenant_id`, `workspace_id`, RLS FORCE hình workspace, grant trong
   migration, test âm xuyên tenant và xuyên workspace.

## Hệ quả

- Ticket `ai-automation/issues/01`. Bước 7 (AI-11) điền BM04 thành bản nháp có trường.
- Hồ sơ PO cũ không có giá: trường rỗng, bước soạn PO nêu khoảng trống.
