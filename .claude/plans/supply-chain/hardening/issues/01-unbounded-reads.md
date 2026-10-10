# 01 — Đọc không giới hạn thành đọc theo trang

Status: resolved
Blocked by: —
Area: supply-chain

## Mục tiêu

Bốn chỗ đọc không có trần (mục "Unbounded reads" và "Pending-approvals card" trong
`.claude/plans/supply-chain.md`, phần Open):

- `list_active` đọc mọi hồ sơ PO đang chạy, rồi `bulk_sla_clock_started_at` và
  `bulk_latest` bind một tham số cho mỗi hồ sơ: quá 32 767 thì asyncpg từ chối, API
  trả 500 (fail closed, nhưng là một trang chết).
- Lịch sử chuyển trạng thái của một hồ sơ (PO và hồ sơ phát triển) trả cả lịch sử.
- Brief đọc 50 yêu cầu duyệt và mọi hồ sơ đổi trạng thái trong 24 giờ, chỉ hiện 10.
- Thẻ "Đang chờ duyệt" của trang Hồ sơ PO gọi `listApprovals({limit: 200})` rồi lọc
  ở trình duyệt: tenant có hơn 200 yêu cầu chờ thì thẻ nói "Không có" sai.

## Tiêu chí chấp nhận

- [x] Hồ sơ đang chạy (PO và hồ sơ phát triển) đọc theo trang keyset
      (`handlers.every_page`, `ACTIVE_CASES_PAGE = MAX_PAGE_SIZE = 200`); mỗi bulk read
      nhận ids của một trang, nên số tham số không phụ thuộc số hồ sơ.
- [x] Lịch sử chuyển trạng thái là `Page`, mới nhất trước, `limit`/`cursor` như mọi list.
- [x] Brief đọc tối đa `ENTRIES_SHOWN` (10) yêu cầu duyệt và 10 hồ sơ đổi trạng thái mới
      nhất; `total` vẫn đếm hết; view nói trần (`entries_shown`).
- [x] `GET /po-cases/{id}/approvals` lọc ở server (payload `po_case_id`), trả
      `visible/total/items`; web không còn gọi `listApprovals` cho thẻ này.
- [x] Mỗi ORDER BY mới có index dẫn bằng `tenant_id, workspace_id` (`d9e136c14d83`).
- [x] Negative: phân trang không vượt tenant/workspace; route mới 403 khi thiếu
      `po_case.read`, `visible=false` khi thiếu `approvals.read`, 404 với hồ sơ không
      phải của mình.

## Comments

**2026-10-08 (agent, Đạt giao quyết tạm):**

- **Không thêm method `list_active` cho PO.** `list_page` với `active_only=True` đã là
  câu SQL "đang chạy" của Control Tower và drill-down của nó; `assess_active_cases`
  đọc nó từng trang. Port bỏ `list_active`. Hồ sơ phát triển giữ `list_active` nhưng
  nhận `PageRequest` và trả `Page`.
- **Mới nhất trước cho lịch sử** (quyết tạm): trang đầu là chỗ hồ sơ đang đứng, "Tải
  thêm" lùi về quá khứ. `ProductReviewReconcile._requester` đi từng trang tới lần
  chuyển gần nhất vào trạng thái hiện tại (thường ở trang đầu).
- **Brief:** đọc đúng số dòng nó hiện (một chủ: `ENTRIES_SHOWN`). Nhóm yêu cầu duyệt
  hiện 10 yêu cầu MỚI NHẤT (trước đây: 10 yêu cầu chờ lâu nhất trong 50 mới nhất,
  một lựa chọn không ai đặt ra); nhóm "đổi trạng thái" đếm hết bằng `count(*)` trên
  cùng truy vấn DISTINCT ON.
- **Lọc theo payload ở nền tảng:** `SqlPendingApprovalQuery.list_pending_by_type_prefix`
  thêm `payload_match` (key, value), dùng chung truy vấn và quy tắc audience của inbox;
  ứng viên upstream lên `codebase` (ADR 0011 E1).
- **Index** (`d9e136c14d83`): `ix_po_case_state_transitions_case_page`,
  `ix_product_dev_case_transitions_case_page` (tenant, workspace, case, occurred_at
  DESC, id DESC); `ix_product_dev_cases_page` dựng lại có `workspace_id`. Hồ sơ PO
  đang chạy dùng `ix_po_cases_page` đã có workspace từ `62cdcf3bf2d2`.
- **Còn lại, chưa làm:** Attention Queue và Control Tower vẫn trả mọi hồ sơ bị gắn cờ
  trong một response (đọc đã có trần theo trang, response thì chưa).
- **Mutation:** bulk read với trang 200 thay vì `ACTIVE_CASES_PAGE` → unit
  `test_active_cases_are_assessed_a_page_at_a_time_with_bounded_bulk_reads` và
  integration `test_the_active_cases_are_read_a_page_at_a_time_inside_the_callers_workspace`
  đỏ; brief đọc `limit=1000` → `test_the_brief_reads_only_the_newest_changes…` đỏ;
  bỏ `total=max(...)` → đỏ; bỏ `payload_match` trong handler → endpoint test đỏ.
