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

## Sửa đổi 2026-10-09 (tạm, lát AI-01; lead quyết các điểm ADR để mở)

1. **Trường nào là "giá"** có một chủ: `PRICE_FIELDS` trong
   `dw_supply_chain.domain.commercial` (đơn giá, số tiền, tổng, thành tiền, % cọc, điều
   khoản thanh toán, số tài khoản, chủ tài khoản, ngân hàng). Thiếu
   `supply_chain.commercial.read` thì handler làm rỗng các trường này **ngay ở tầng
   application** (không qua tới presentation), view trả `{"value": null, "redacted": true}`.
   Tiền tệ, Incoterm, MOQ, thời gian sản xuất, ngày giao dự kiến là dữ liệu kế hoạch, ai
   đọc được hồ sơ thì thấy.
2. **Vai:** `.read` cho `sc_operator`, `sc_finance`, `sc_bod`; `.write` cho `sc_operator`,
   `sc_finance` (BGĐ duyệt, không nhập giá); `.write` ở phía vận hành của
   `sod_sc_rules_vs_operations`. Migration `82221a867e62`.
3. **BM04:** lưu một phiên bản cần `product_case.write`; giá và tiền tệ cần thêm
   `commercial.write`. Người lưu không có scope giá thì phiên bản mới **mang theo** giá và
   tiền tệ của phiên bản trước (không xóa, không bịa). Giá cần tiền tệ (domain và CHECK
   `ck_product_profiles_price_has_currency`). Schema của tenant ghi đè bằng
   `PUT /bm04-schema`, scope `action_duties.write` như các luật quy trình khác (cùng mẫu
   policy đóng gói); schema không được khai trường trùng cột có kiểu hay trường giá.
   `complete_profile` vẫn đòi file `product_profile_bm04` tải lên; dựng file BM04 từ
   biểu mẫu là việc của AI-03 (mẫu) và AI-11 (bước 7).
4. **Hồ sơ PO:** đổi điều khoản và đơn giá dòng là một lệnh `PUT /po-cases/{id}/commercial`
   (`commercial.write`), không tăng `version` của hồ sơ (phiên bản hồ sơ là của trạng thái;
   AI-05 quyết đề xuất cũ khi nào hết hiệu lực). Đơn giá dòng cần tiền tệ của PO. Thanh
   toán: phiên bản theo loại (`deposit | final`), chứng từ trích dẫn phải là
   `deposit_docs` (cọc) hoặc `payment_docs` (thanh toán cuối) **của chính PO** (handler,
   rồi FK ghép). Giao diện ghi thanh toán để AI-15.
5. **NCC:** người liên hệ đọc bằng `po_case.read`, ghi bằng `commercial.write`; tài khoản
   ngân hàng đọc và ghi bằng scope giá. Số tài khoản lưu đã chuẩn hóa
   (`normalize_account_number`, CHECK `^[0-9A-Z]{6,34}$`), so bằng `same_account`. Audit
   chỉ ghi id và phiên bản, không ghi số tiền hay số tài khoản. Chưa có giao diện NCC (ON-01
   nạp, AI-07 dùng người liên hệ).
6. **Zalo:** câu trả lời dựng từ `presentation/zalo_views` (một chủ của danh sách trường
   được phép); test giữ danh sách đó rời với `PRICE_FIELDS`.

## Sửa đổi 2026-10-09 (tạm, lát AI-11; BM04 điền sẵn)

1. BM04 của bước 7 là bản nháp: mỗi ô một nguồn (hồ sơ, chứng từ và trích dẫn, hoặc mô hình
   đọc từ mục bằng chứng có trích dẫn). Hai nguồn khác giá trị là mâu thuẫn và ô trống; code
   không chọn bên nào.
2. Giá, tiền tệ, MOQ, thời gian sản xuất, Incoterm chỉ do code chép từ chứng từ; mô hình không
   được hỏi, không thấy giá, và ô thương mại nó trả về bị loại ở cả hai lớp.
3. Payload của approval không lọc theo scope, nên mâu thuẫn về giá chỉ nêu tên hai chứng từ;
   số giá code biết bị che trong mọi trích dẫn khác.
4. Duyệt bước 7 ghi một phiên bản `product_profiles` trong cùng giao dịch với bước: giá chỉ khi
   người duyệt có `supply_chain.commercial.write` (không thì giữ giá phiên bản trước, như thẻ
   BM04); ô bắt buộc của schema tenant còn thiếu thì không ghi phiên bản (audit
   `product_profile.not_saved`), người điền thẻ BM04.
