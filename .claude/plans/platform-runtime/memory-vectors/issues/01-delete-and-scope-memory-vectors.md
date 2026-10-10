# 01 — Xóa vector memory theo id và theo tenant; ranker lọc theo id đã gọi lên

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Point trong collection `dw_memory` không sống lâu hơn dòng `memory.items` của nó (hết
hạn, bị thay thế, tenant rời đi), và `nearest` chỉ xếp lại đúng các id mà SQL đã gọi lên.
Spec, mục "Hiện trạng" và "Quy tắc và kiểm soát".

## Việc cần làm

1. `packages/python/dw_memory/src/dw_memory/ranking.py`:
    - Protocol mới `MemoryVectorPurgePort` với `delete(memory_ids)` và
      `delete_by_tenant(tenant_id)`. Không gộp vào `MemoryRankerPort`: recall chỉ cần
      `nearest` (`code-quality.md`, interface segregation).
    - `MemoryRankerPort.nearest` nhận thêm `candidate_ids: Sequence[UUID]` (bắt buộc,
      không có mặc định: một bộ lọc quên được là một bộ lọc sẽ bị quên).
    - Sửa docstring module: câu "A superseded memory may well still sit in the index"
      không còn đúng sau ticket này.
2. `adapters/qdrant_ranker.py`:
    - `nearest`: thêm `models.HasIdCondition(has_id=[str(i) for i in candidate_ids])`
      vào `must`, cạnh ba điều kiện tenant, workspace, worker (không thay chúng);
      `limit=len(candidate_ids)`; tập rỗng thì trả `()` không gọi Qdrant.
    - `delete(memory_ids)`: `PointIdsList`. `delete_by_tenant`: `FilterSelector` theo
      `tenant_id` (field đã có payload index). Collection chưa tồn tại thì là việc đã
      xong, không lỗi. Lỗi khác thì raise: người gọi quyết định (khác `index`, vốn nuốt
      lỗi vì chạy sau commit).
3. `dw_memory/service.py`:
    - `_ordered` truyền `candidate_ids=[item.memory_id for item in found]`.
    - Sau khi giao dịch `propose` commit, nếu `superseded` không rỗng và service có
      purge port, gọi `delete(superseded)`. Lỗi thì log cảnh báo, không làm hỏng
      `propose` (memory đã commit; point thừa chỉ tốn chỗ vì ranker giờ chỉ xếp id SQL
      chọn). Thêm field `vector_purge: MemoryVectorPurgePort | None = None` vào
      `MemoryService`, nối ở worker.
4. `dw_memory/retention.py`: `SqlMemoryRetention` nhận
   `vector_index: MemoryVectorPurgePort | None`, theo đúng mẫu `SqlKnowledgeRetention`. `_delete_batch`
   trả về id đã xóa (`RETURNING memory_id`), rồi xóa point của các id đó sau commit.
   Nếu Qdrant lỗi: ghi log và đếm, dòng đã xóa không khôi phục; ghi trong docstring rằng
   lượt quét sau không thấy lại các id đó, và vì vậy offboarding vẫn là lưới cuối.
5. `apps/worker/src/dw_worker/consumers/offboarding.py`: `TenantOffboardingLane` nhận
   thêm `memory_vectors: VectorPurgePort` (cùng Protocol `delete_by_tenant` đã có) và
   gọi sau `vector_index.delete_by_tenant`. `main.py` nối cùng một `QdrantMemoryRanker`
   cho cả ba chỗ (handler, retention, offboarding). Không Qdrant thì không nối.
6. Docstring `TenantOffboardingLane` và `offboarding.py` (dw_platform) nói hai
   collection, không một.

## Tiêu chí chấp nhận

- Test tích hợp với Qdrant thật (`packages/python/dw_memory/tests/integration/test_qdrant_ranker.py`
  và test lane offboarding của worker):
    - offboarding: tenant A và B mỗi bên có point; chạy lane cho A; A còn 0 point, B còn
      nguyên;
    - retention: memory quá hạn bị xóa dòng thì mất point; memory còn hạn giữ point;
    - supersession: memory bị thay (cùng `fact_key`, cùng subject) mất point; memory mới
      còn point;
    - `nearest`: một worker có nhiều memory ở subject khác hơn tập `found`; kết quả chỉ
      chứa id thuộc `candidate_ids`, đủ cả tập;
    - `test_an_id_the_ranker_invents_cannot_add_a_row` và mọi test tenant, worker của
      ranker giữ xanh.
- Chứng minh đỏ, ghi lệnh và kết quả dưới `## Comments`: bỏ lần lượt bốn chốt ở bảng
  của spec, mỗi lần một test đỏ; hoàn nguyên.
- `uv run pytest packages/python/dw_memory apps/worker -m integration` và `make ci` xanh;
  `make infra-down` cho repo này khi xong.
- Không tên sản phẩm hay context nào trong tệp đã sửa.

## Nguồn

- Audit harness 6/10/2026, khu `memory-store` gap 1 (real=true), khu `retrieval` gap
  "The vector ranker asks Qdrant the wrong question", khu `file-memory-and-instructions`
  gap 1.
- `qdrant_ranker.py:97-174`, `ranking.py`, `service.py:132-216, 266-301, 487-522`,
  `retention.py:41-104`, `apps/worker/src/dw_worker/consumers/offboarding.py:100-161`,
  `apps/worker/src/dw_worker/main.py:140-200`.
- `.claude/rules/failure-modes.md` #1, #3, #5, #6.

## Comments

### 2026-10-06: xong trên `feat/platform-hardening`, commit `55bd789`

Đã làm đủ sáu mục. Ba chỗ khác ticket, có lý do:

- `nearest` **thay** `limit` bằng `candidate_ids`, không thêm cạnh nó: adapter dùng
  `limit=len(candidate_ids)`, nên giữ `limit` là hai nguồn cho một con số.
- `SqlMemoryRetention.vector_index` và `TenantOffboardingLane.memory_vectors` không có
  mặc định: composition root phải viết `None` ra, một tham số quên được là một point bị
  giữ mãi. `MemoryService.vector_purge` giữ `= None` như ticket (API dựng service chỉ
  để recall).
- Fixture Qdrant chung chuyển vào `dw_memory/tests/integration/conftest.py`, đọc URL từ
  `.env` (qua `runtime_urls`) và **fail** khi không tới được, như Postgres. Trước đây nó
  đọc `os.environ` với mặc định 6333; máy này Qdrant ở 16333 nên mọi test ranker đã
  **skip** im lặng (`failure-modes.md` #0, #3).

Đo được (failure-modes #4): `index` upsert với `wait=False`, nên đọc ngay sau có thể không
thấy. Sáu upsert rồi một `nearest` trả `()`; hai giây sau trả đủ. Fixture `indexed`
chờ point hiện ra; các test `nearest` cũ trước đây xanh nhờ may.

Kiểm chứng (repo này, infra `dw_codebase`):

- Đỏ trước: `uv run pytest packages/python/dw_memory/tests/integration apps/worker/tests/integration -m integration`
  → `TypeError: nearest() got an unexpected keyword argument 'candidate_ids'` và các test
  mới đỏ (lượt đầu Docker còn khởi động; sau đó đỏ đúng chỗ).
- `uv run pytest packages/python/dw_memory apps/worker packages/python/dw_platform/tests/integration/test_offboarding.py -m integration`
  → 76 collected, 76 passed, 0 skipped. Gồm `test_an_id_the_ranker_invents_cannot_add_a_row`
  và các test tenant/worker của ranker.
- `make ci` → exit 0: ruff, prettier, mypy (367 files), unit 903 passed / 3 skipped,
  import-linter 5 kept / 0 broken, declared-deps 11 packages, contract 2 passed,
  eval-smoke `platform_smoke@1.0.0` 4/4, release manifest up to date. Lần đầu đỏ ở prettier
  trên tám tệp plan của `47fa4f6`; đã sửa riêng ở commit `style(plan)`.

Chứng minh đỏ (`scratchpad/mutate.sh`: sửa, chạy đúng test, chép lại bản gốc, `cmp`
khớp; `git diff` sau cùng không còn đột biến):

| Chốt bị gỡ                                                                  | Test đỏ                                                                     |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| lời gọi `memory_vectors.delete_by_tenant` trong `TenantOffboardingLane.run` | `test_offboarding_deletes_the_leaving_tenants_memory_vectors_and_no_others` |
| `await self._purge_vectors(deleted)` trong `SqlMemoryRetention.prune`       | `test_an_expired_memory_loses_its_vector_and_a_live_one_keeps_it`           |
| `await self._purge_vectors(superseded)` trong `MemoryService.propose`       | `test_the_superseded_memory_loses_its_vector_and_the_new_one_keeps_it`      |
| `HasIdCondition` trong `nearest`                                            | `test_only_the_ids_sql_recalled_are_ranked_and_all_of_them`                 |
| service chỉ đưa id đầu tiên (`[:1]`) làm `candidate_ids`                    | `test_the_ranker_is_handed_exactly_the_rows_recall_found`                   |
| bỏ `if not candidate_ids: return ()`                                        | `test_no_candidates_asks_the_store_nothing`                                 |
| bỏ điều kiện `tenant_id` trong `nearest`                                    | `test_another_tenants_memory_is_never_returned`                             |
| nuốt lỗi quanh xóa memory trong lane (`try/except: pass`)                   | `test_a_memory_vector_purge_that_fails_is_reported_not_completed` (unit)    |

`reviewing-feature-security`: (1) tenant: filter tenant/workspace/worker dựng trong
adapter từ `AccessContext`; id cho `delete` đến từ SQL dưới RLS hoặc `worker_drain`;
test âm: id lạ không trả gì, B còn nguyên khi A offboard. (2) authorization: không route
hay quyết định mới, chỉ lane worker. (3) autonomy: agent không gọi được xóa; ranker chỉ
đổi thứ tự. (4) untrusted: `candidate_ids` là id dòng SQL, không phải output model.
(5) audit: sự kiện supersession không đổi, cùng giao dịch; point là index dẫn xuất.
(6) lifecycle: tạo ở handler outbox, xóa ở supersession, retention, offboarding; Qdrant
xuống thì retention vẫn xóa dòng, supersession vẫn commit, offboarding báo `failed`, mỗi
cái có test. Không đụng Dockerfile, lockfile hay compose.

Còn lại, đúng như docstring: point mà retention hoặc supersession không xóa được (Qdrant
xuống) ở lại đến khi tenant offboard; lượt quét sau không thấy lại id đó.

### 2026-10-06: kiểm chứng độc lập, sửa một lỗ hổng

- Outbox giao lại (at-least-once) một event mà memory của nó đã bị memory sau thay:
  `_already_decided` trả dòng đã lưu (có `valid_until`), handler `index` lại nó và
  point vừa bị supersession xóa quay về. Handler giờ chỉ index memory còn mở
  (`valid_until is None`); test đơn vị
  `test_a_redelivered_memory_that_was_since_superseded_is_not_indexed_again` đỏ khi gỡ
  chốt. Còn lại: hai worker cùng lúc (A index sau khi B đã thay và xóa A) vẫn có thể để
  lại point; offboarding là lưới cuối.
- `test_an_expired_memory_loses_its_vector_and_a_live_one_keeps_it` index với
  `wait=False` rồi không chờ, nên khi gỡ purge, "điểm hết hạn không còn" có thể xanh vì
  upsert chưa áp. Giờ dùng fixture `indexed` (chờ point hiện ra).
- Đột biến lại, mỗi cái đỏ rồi hoàn nguyên: `HasIdCondition`, lời gọi
  `memory_vectors.delete_by_tenant`, `_purge_vectors` của retention, chốt mới ở handler.
  Tích hợp 76 passed (hai lượt), `make ci` exit 0.
