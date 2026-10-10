# Memory chờ duyệt tới tay người: `memory.review` trong hộp approval

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm. Nguồn: audit harness 6/10/2026 (khu
`memory-store`, `file-memory-and-instructions`, `reuse`: cùng một gap, ba lần), kiểm lại
trên `main` (`4cb45dc`) của repo này. `failure-modes.md` #1 đã đếm "the memory REVIEW
queue" từ trước.

## Hiện trạng (đã kiểm trong code)

- `MemoryWritePolicy.evaluate` (`dw_memory/policy.py:45-60`) trả REVIEW cho mọi candidate
  `restricted` và mọi candidate có độ tin 0.50–0.80.
- `MemoryService.propose` (`dw_memory/service.py:132-216`) ghi một dòng
  `memory.write_candidates` với `decision = 'review'` và một audit
  `memory.write_held_for_review`. Chỗ đọc duy nhất của bảng là `_already_decided`
  (`service.py:219-264`, kiểm idempotency). Không route, không UI, không service nào duyệt
  hay từ chối; `apps/api/src/dw_api/routes/v1/memory.py` chỉ đọc. "Người duyệt" hôm nay
  nghĩa là "bỏ".
- Hộp approval của nền tảng chở được approval không gắn run: `approval_requests.run_id`
  nullable (`0001_platform_baseline.sql:170-183`) và `ApproveAndResumeService.decide`
  (`dw_agent_runtime/approval_flow.py`) bỏ qua bước resume khi `run_id` là NULL. Nhưng với
  approval không run, quyết định không kích hoạt gì: không có chỗ móc hậu quả.
- `PlatformUnitOfWork` có `outbox` (`dw_platform/application/ports.py:289-299`), ghi cùng
  giao dịch với approval. Worker phát outbox theo `event_type`
  (`apps/worker/src/dw_worker/consumers/outbox.py:88`).
- Nền tảng **không có** `required_scope` đóng dấu trên approval (đó là ADR của một sản
  phẩm, không có ở đây). Quyền quyết là scope `approvals.decide`
  (`db/migrations/sql/0001_platform_reference.sql:57-68`).
- `approval-audit-and-workspace` 01 (quyết định ghi audit) và 02 (approval đọc theo
  workspace) còn `ready-for-agent` ở repo này.

## Thiết kế

Kết luận: cơ chế approval chở được việc này mà không cần thay đổi lớn. Một chỗ móc trung
tính và một hậu quả đi qua outbox, cùng kiểu ghi memory bất đồng bộ đã có.

1. **Tạo approval khi REVIEW.** Trong giao dịch của `propose`, khi quyết định là REVIEW,
   chèn một `approval_requests`: `approval_type = 'memory.review'`, `run_id = NULL` (run
   sinh ra candidate đã xong; gắn nó sẽ làm `decide` đòi run đang chờ duyệt),
   `requested_by` là actor của candidate, `payload` chỉ mang định danh và mã
   (`candidate_id`, `worker_id`, `memory_type`, `classification`), không mang nội dung.
   Nội dung đọc qua route memory, có kiểm clearance.
2. **Chỗ móc trung tính.** `ApproveAndResumeService.decide`: với approval không có run,
   ghi một outbox event `"{approval_type}.decided"` (payload: `approval_id`, `outcome`,
   `decided_by`, `comment` rỗng hay không) trong cùng UoW với quyết định. Không có
   `if approval_type == 'memory.review'` trong service (`code-quality.md`, open/closed):
   context nào muốn hậu quả thì đăng ký handler cho event type của mình ở composition root.
3. **Hậu quả.** Worker đăng ký handler `memory.review.decided`:
    - duyệt → `MemoryService.promote(candidate_id, decided_by)`: đi qua đúng đường ghi đã
      kiểm của `propose` (kiểm bằng chứng, kiểm phân loại, supersession, `item_evidence`,
      audit) — tách phần ghi item của `propose` thành một hàm dùng chung, không chép.
      Chỉ bỏ qua ngưỡng độ tin, không bao giờ bỏ qua bằng chứng. Idempotent theo
      `candidate_id` (`write_candidates.memory_id` đã có thì trả kết quả cũ);
    - từ chối → không item; audit `memory.review_rejected`;
    - bằng chứng không còn hợp lệ lúc duyệt → không item, audit
      `memory.review_failed`, `UndeliverableEventError` (thử lại không làm nó đúng).
4. **Quyền.** Ngoài `approvals.decide`: người quyết phải có clearance đọc được phân loại
   của candidate (không duyệt điều mình không được đọc). Kiểm ở chỗ quyết định xảy ra,
   không chỉ ẩn nút.

**Điểm dừng.** Nếu khi làm thấy `decide` không thể ghi outbox trong cùng UoW mà không đổi
hợp đồng của `PlatformUnitOfWork`, hoặc approval không run vướng ràng buộc ở ticket 02 của
`approval-audit-and-workspace`, dừng lại: ghi khoảng trống thiết kế vào `## Comments`
của ticket, không làm nửa vời. Phương án dự phòng (audit đề xuất): policy trả REJECT với
lý do "chưa có hàng đợi duyệt" cho tới khi đường này có.

## Trong phạm vi

- `dw_memory/service.py` (REVIEW tạo approval; `promote`, `reject` dùng chung đường ghi),
  `dw_agent_runtime/approval_flow.py` (outbox cho approval không run),
  `apps/worker/src/dw_worker/consumers/memory.py` và `main.py` (handler),
  `apps/api/src/dw_api/routes/v1/memory.py` (đọc một candidate, kiểm clearance),
  migration nếu cần (cột hay CHECK; mở rộng CHECK `decision` của lát
  `memory-write-trust` nếu thêm giá trị).

## Ngoài phạm vi

- Màn hình duyệt riêng cho memory: hộp `/approvals` chung là đủ; liên kết sang màn của
  context là ticket `approval-inbox-link`.
- Producer `memory.candidate_proposed`: việc của context đầu tiên.

## Quy tắc và kiểm soát

| Chốt                                         | Test đỏ khi gỡ                                                                              |
| -------------------------------------------- | ------------------------------------------------------------------------------------------- |
| REVIEW tạo đúng một approval `memory.review` | Bỏ phần chèn approval → test "candidate REVIEW có approval chờ" đỏ                          |
| Duyệt ghi đúng một item                      | Bỏ handler hay `promote` → 0 item, test đỏ; giao event hai lần → vẫn 1 item                 |
| Từ chối không ghi item                       | `promote` gọi cả khi từ chối → test đỏ                                                      |
| Duyệt vẫn kiểm bằng chứng                    | `promote` bỏ `evidence_store.record` → candidate với chunk đã xóa vẫn ghi, test đỏ          |
| Tenant khác không quyết được                 | Bỏ RLS hay đọc approval không qua tenant → người của tenant B quyết approval của A, test đỏ |
| Clearance đủ mới quyết được                  | Bỏ kiểm clearance → người clearance `internal` duyệt candidate `restricted`, test đỏ        |
| Payload approval không mang nội dung         | Chép `content` vào payload → test đọc `/approvals` dưới clearance thấp đỏ                   |
| Quyết định có audit                          | (ticket 01 của `approval-audit-and-workspace`) + audit `memory.*` của hậu quả               |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved` (hoặc ghi khoảng trống thiết kế theo Điểm dừng), chứng
  minh đỏ dưới `## Comments`.
- Integration xanh cho `dw_memory`, `dw_agent_runtime`, `apps/worker`, `apps/api`;
  `make ci` xanh.
- `failure-modes.md` #1: dòng "the memory REVIEW queue" ghi là đã có người đọc (đếm giữ
  nguyên). Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- `approval-audit-and-workspace` 01 và 02; `memory-write-trust` 01.
- Từ `memory-write-trust` 01 (đã xong): candidate REVIEW **chưa** qua
  `EvidenceStorePort.record` lúc đề xuất, nên `promote` là lần đầu bằng chứng, workspace
  và phân loại của nó được kiểm; phép so phân loại sau `record` phải nằm trong phần ghi
  item dùng chung. `MemoryCandidate` không còn `confidence`; độ tin là
  `PolicyOutcome.confidence` (đã lưu ở `write_candidates.confidence`). Thêm giá trị cho
  `decision` thì sửa cả CHECK `ck_write_candidates_decision` (test so catalog sẽ đỏ).

## Câu hỏi còn mở

1. ~~`memory.` có vào `strict_approval_prefixes` không?~~ Làm theo đề xuất: có (6/10/2026;
   Đạt vẫn có thể đổi): nối ở `dw_api/bootstrap/runtime.py`, ghim bằng
   `test_memory_review_wiring.py`. Đổi lại thì đổi một dòng ở đó.

## Danh sách ticket

| #   | Ticket                                                                                                                              | Status   | Blocked by                                                 |
| --- | ----------------------------------------------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------- |
| 01  | [Candidate REVIEW thành approval `memory.review`; quyết định ghi hay bỏ qua đường ghi đã kiểm](issues/01-memory-review-approval.md) | resolved | approval-audit-and-workspace 01, 02; memory-write-trust 01 |
