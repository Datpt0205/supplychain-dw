# Vector của memory bị xóa cùng dòng của nó; ranker chỉ xếp lại id SQL đã chọn

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm. Nguồn: audit harness 6/10/2026 (khu
`memory-store` và `file-memory-and-instructions`, gap đã qua lượt phản biện), kiểm lại
trên `main` (`4cb45dc`) của repo này.

## Hiện trạng (đã kiểm trong code)

- `QdrantMemoryRanker`
  (`packages/python/dw_memory/src/dw_memory/adapters/qdrant_ranker.py`) chỉ có
  `ensure_ready`, `index`, `nearest`. Không có lệnh xóa nào.
- Worker nối index thật: `apps/worker/src/dw_worker/main.py` truyền
  `_build_memory_index(settings)` vào `memory_handlers`, nên mỗi AUTO_WRITE upsert một
  point vào collection `dw_memory` (`consumers/memory.py:151-158`).
- Retention: `SqlMemoryRetention._delete_batch` (`dw_memory/retention.py:84-104`) chỉ
  xóa dòng `memory.items`. Ngược lại `SqlKnowledgeRetention` đã nhận `vector_index` và
  xóa point cùng dòng (`main.py:179-186`): mẫu để chép.
- Supersession: `MemoryService._close_superseded` (`service.py:266-301`) đóng
  `valid_until`, giữ dòng (đúng: "hôm thứ Ba ta tin gì" vẫn trả lời được). Point của
  memory đã đóng vẫn nằm trong Qdrant và vẫn được `nearest` trả về.
- Offboarding: `TenantOffboardingLane.run` (`apps/worker/src/dw_worker/consumers/offboarding.py:135-161`)
  chỉ gọi `vector_index.delete_by_tenant` của collection knowledge. Lượt xóa SQL đã xóa
  `memory.items` (`dw_platform/adapters/persistence/offboarding.py`, `_ORDERED_FIRST`),
  nên embedding của nội dung tenant sống tiếp sau khi tenant rời đi
  (`failure-modes.md` #6).
- `nearest` (`qdrant_ranker.py:138-174`) lọc theo tenant, workspace, worker, rồi lấy
  `limit=len(found)` điểm gần nhất trong TOÀN BỘ point của worker đó, gồm memory của
  subject khác và memory đã bị thay thế. Kết quả có thể không chứa id nào trong số
  `found`, và `rank_by` khi đó trả về thứ tự cũ: ranker im lặng không làm gì.
  Không test nào bắt được, vì test service chạy với ranker giả.

Vì sao hôm nay chưa thành sự cố: chưa có gì phát `memory.candidate_proposed`. Lát này
phải có trước producer đầu tiên.

## Mục tiêu

1. Port ranker có xóa theo id và theo tenant; adapter Qdrant làm cả hai.
2. Ba đường xóa gọi nó: retention (sau khi xóa dòng), supersession (memory bị đóng mất
   point), offboarding (mọi point của tenant).
3. `nearest` chỉ xếp lại đúng tập id SQL đã gọi lên: lọc bằng `HasIdCondition` theo các id
   đó, `limit` bằng số id đó. Không bao giờ tìm trên cả collection của worker.

## Trong phạm vi

- `dw_memory/ranking.py`: Protocol mới cho phía xóa, tách khỏi `MemoryRankerPort`
  (interface segregation: recall chỉ cần `nearest`, retention và offboarding chỉ cần xóa).
- `qdrant_ranker.py`: `delete(ids)`, `delete_by_tenant(tenant_id)`; `nearest` nhận
  `candidate_ids` và lọc theo chúng.
- `SqlMemoryRetention`, `MemoryService` (đường supersession), `TenantOffboardingLane`,
  `apps/worker/src/dw_worker/main.py` (nối).
- Test tích hợp với Qdrant thật (`make infra-up` của repo này).

## Ngoài phạm vi

- Thêm `query` vào `MemoryRecallPort` / middleware recall: chưa có agent loop thật nào
  gọi recall; làm cùng caller đầu tiên.
- Đổi payload point (subject, classification): không cần khi ranker chỉ xếp tập SQL chọn.
- Bỏ hẳn ranker: audit nêu như một phương án; giữ vì nó đã có test và chỉ đổi thứ tự.

## Quy tắc và kiểm soát

| Chốt                                    | Test đỏ khi gỡ                                                                                                                         |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Offboarding xóa point memory của tenant | Bỏ lời gọi xóa memory trong `TenantOffboardingLane.run` → test "A mất hết point, B còn nguyên" đỏ                                      |
| Retention xóa point                     | Bỏ lời gọi xóa trong `SqlMemoryRetention` → point của memory hết hạn còn trong Qdrant, test đỏ                                         |
| Supersession xóa point                  | Bỏ lời gọi xóa trên đường supersession → memory bị thay vẫn còn point, test đỏ                                                         |
| Ranker chỉ xếp id SQL đã chọn           | Bỏ `HasIdCondition` → test "worker có nhiều memory subject khác hơn tập found" đỏ (ranker trả id ngoài tập, thứ tự không theo câu hỏi) |
| Id lạ từ ranker không thêm dòng         | `test_an_id_the_ranker_invents_cannot_add_a_row` giữ xanh                                                                              |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved`, ghi commit và các chứng minh đỏ dưới `## Comments`.
- `uv run pytest packages/python/dw_memory apps/worker -m integration` và `make ci` xanh.
- Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- Không chờ lát nào. Lát `memory-write-trust` và `memory-review-queue` đọc cùng
  `service.py`; làm lát này trước để tránh xung đột.

## Câu hỏi còn mở

1. Xóa point khi supersession hay giữ đến retention? Đã làm theo đề xuất (ticket 01): xóa ngay. Dòng đóng vẫn
   giữ để giải thích quyết định cũ, nhưng recall không bao giờ đọc memory đã đóng
   (`valid_until` lọc trong SQL), nên point của nó không có người đọc
   (`failure-modes.md` #1, #6).

## Danh sách ticket

| #   | Ticket                                                                                                                  | Status   | Blocked by |
| --- | ----------------------------------------------------------------------------------------------------------------------- | -------- | ---------- |
| 01  | [Xóa vector memory theo id và theo tenant; ranker lọc theo id đã gọi lên](issues/01-delete-and-scope-memory-vectors.md) | resolved | —          |
