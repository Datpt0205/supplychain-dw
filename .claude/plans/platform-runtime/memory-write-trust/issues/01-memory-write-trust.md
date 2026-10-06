# 01 — Phân loại theo bằng chứng, độ tin do workflow tính, CHECK, lớp retention trung thực

Status: resolved
Blocked by: platform-runtime/memory-vectors 01
Area: platform-runtime

## Mục tiêu

Một memory chỉ được lưu ở phân loại, workspace và độ tin mà code đã kiểm, và các cột tập
cố định của nó bị database từ chối khi sai. Spec, mục "Quy tắc và quyết định" và "Quy tắc
và kiểm soát".

## Việc cần làm

1. **Bằng chứng** — `packages/python/dw_knowledge/src/dw_knowledge/adapters/evidence_store.py`:
    - Truy vấn chunk join `knowledge.documents`, lấy thêm `chunks.workspace_id`,
      `documents.classification`, `documents.scope`.
    - Từ chối (`DomainError`, nêu `chunk_id`) chunk có `workspace_id` khác workspace của
      người gọi, trừ khi tài liệu `scope = 'global'` (cùng luật với
      `KnowledgeGateway.search`; đọc luật đó từ một chỗ nếu tách được, đừng chép lần hai).
    - Ghi `knowledge.evidence.classification` từ tài liệu, không từ `ref.classification`.
    - `record` trả về phân loại chặt nhất trong các tài liệu được trích (thứ tự lấy từ
      thang clearance ở `dw_knowledge/contracts.py`, không gõ lại). Cập nhật
      `EvidenceStorePort` (`dw_memory/ports.py`) theo.
2. **Phân loại** — `dw_memory/service.py`: sau `record`, nếu phân loại candidate khai
   thấp hơn phân loại trả về thì raise `DomainError` (giao dịch rollback, không item,
   không candidate row). `MemoryCandidate.classification` thành `Literal` của ba giá trị,
   hoặc validator đọc khóa thang clearance; giá trị lạ bị từ chối khi parse.
3. **Độ tin** — `dw_memory/policy.py`, `dw_memory/service.py`, `consumers/memory.py`:
    - Docstring `MemoryCandidate`, `MemoryWritePolicy` và `MemoryProposePort` nói: độ tin
      do code workflow tính từ tín hiệu đã kiểm, không bao giờ chép từ đầu ra model.
    - Cưỡng chế ở cửa vào duy nhất từ producer: `MemoryCandidatePayload` lên
      `schema_version` `1.1`, không còn mang `confidence` thô; mang tín hiệu (đề xuất ở
      spec, Câu hỏi 1). Service tính độ tin từ tín hiệu và từ số bằng chứng đã qua kiểm.
      Payload `1.0` bị từ chối là `UndeliverableEventError` (đóng chặt, không đoán).
    - Nâng `MemoryWritePolicy.policy_version` (hành vi đổi); release manifest đọc nó.
    - `dw_evals/graders.py:199` dựng `MemoryCandidate`: sửa theo hình dạng mới.
4. **Lỗi từ chối không bị thử lại vô hạn**: kiểm handler outbox xử lý `DomainError` từ
   `propose` thế nào; nếu nó đang được thử lại như lỗi hạ tầng thì ánh xạ sang
   `UndeliverableEventError` (bằng chứng sai không thành đúng khi thử lại).
5. **CHECK** — `uv run alembic revision -m "memory fixed-set checks"` (id hex ngẫu nhiên,
   số thứ tự chỉ ở tên tệp): CHECK trên `memory.items.memory_type`, `classification`,
   `memory.write_candidates.decision`, `memory_type`, `classification`. Tên theo
   `NAMING_CONVENTION`. Đo trước dữ liệu có sẵn (DB dev) để migration không hỏng trên
   dòng cũ; downgrade gỡ chúng. Không CHECK cho `retention_policy` (spec).
6. **Retention** — `configs/policies/retention@<bản kế>.yaml`: gỡ `sensitive`,
   `ephemeral`, `legal_hold` khỏi `classes`, ghi chú vì sao và khi nào `legal_hold`
   quay lại. Đổi mọi chỗ ghim tên tệp (`rg "retention@1.4.0"`). Test
   `test_legal_hold_is_never_swept_however_old` chuyển sang chính sách giả có một lớp
   `days: null`, để hành vi vẫn được giữ.

## Tiêu chí chấp nhận

- Test âm, mỗi cái một chốt của spec:
    - candidate `internal` trích chunk của tài liệu `confidential` → bị từ chối, 0 item,
      0 dòng candidate;
    - candidate `confidential` trích đúng tài liệu `confidential` → ghi, item mang
      `confidential`, `knowledge.evidence.classification = 'confidential'` dù ref khai
      `internal`;
    - chunk của workspace W2 làm bằng chứng cho memory ở W1 (cùng tenant) → bị từ chối;
    - chunk của tài liệu global → được chấp nhận;
    - payload có `confidence: 1.0` (dạng model tiêm) → không AUTO_WRITE; payload `1.0` cũ
      → `UndeliverableEventError`;
    - chèn `memory_type = 'bogus'`, `decision = 'maybe'`, `classification = 'public'` →
      `IntegrityError`, tên ràng buộc đúng;
    - test đọc `pg_constraint` so tập giá trị CHECK với `MemoryType`, `WriteDecision`,
      khóa thang clearance;
    - test: mọi lớp memory trong chính sách retention đang ghim có đường gán trong code
      (hôm nay: chỉ `default`).
- Chứng minh đỏ cho từng dòng của bảng "Quy tắc và kiểm soát" (gỡ chốt, test đỏ, hoàn
  nguyên), ghi dưới `## Comments`.
- `make ci` xanh; `make infra-down` cho repo này khi xong.

## Nguồn

- Audit harness 6/10/2026, khu `memory-store`: gap phân loại theo chunk, gap độ tin
  AUTO_WRITE, gap CHECK (real=true, kèm cảnh báo về `retention_policy`), gap lớp
  retention.
- `service.py:87-216, 391-470`, `policy.py`, `ports.py`, `evidence_store.py:55-125`,
  `dw_knowledge/contracts.py:14-37`, `dw_knowledge/tables.py:12-55`,
  `apps/worker/src/dw_worker/consumers/memory.py:87-160`,
  `db/migrations/sql/0001_platform_baseline.sql:97-139`,
  `configs/policies/retention@1.4.0.yaml`, `dw_platform/retention_policy.py`.
- `CLAUDE.md` "Agent and tool rules", "Data model rules"; `failure-modes.md` #1, #2, #3, #7.

## Comments

### 2026-10-06: xong trên `feat/platform-hardening`

Đủ sáu mục. Các chỗ phải chọn, có lý do:

- **Độ tin = độ chứng thực.** `MemoryCandidate` không còn trường `confidence`;
  `MemoryWritePolicy.evaluate` đếm số **tài liệu** khác nhau được trích (không đếm ref:
  lặp một trích dẫn với `evidence_id` mới không thành nguồn thứ hai), trả
  `PolicyOutcome.confidence = 1 - 0.5**n` (thứ tự cho recall) và quyết theo số đếm:
  0 → REJECT, `restricted` → REVIEW, `>= auto_write_sources` (2) → AUTO_WRITE, còn lại
  REVIEW. Mọi ref của một AUTO_WRITE được kiểm trước khi ghi item, một ref sai từ chối
  cả lần ghi, nên khi item commit thì số đếm là số tài liệu đã qua kiểm. Không thêm cờ
  "người đã xác nhận": nó thuộc `memory-review-queue`. `policy_version` 1.0.0 → 2.0.0
  (manifest đọc nó). Hệ quả: một nguồn duy nhất giờ vào REVIEW (trước đây AUTO_WRITE
  nếu producer khai ≥ 0.80); không có producer production nào nên không ai mất gì hôm
  nay, nhưng ngưỡng 2 là một quyết định Đạt cần xác nhận (area file, "Decisions owed").
- **Payload 1.1**: hình dạng `MemoryCandidate` không có `confidence`, `extra="forbid"` nên
  một payload mang `confidence` bị từ chối khi parse (`UndeliverableEventError`), không
  bị bỏ qua im lặng. Payload `1.0` bị từ chối theo `schema_version`. Không thêm trường
  "tín hiệu" riêng: tín hiệu duy nhất kiểm được hôm nay là chính `provenance_refs`.
- **Phân loại**: `CLASSIFICATIONS` và `classification_rank` ở `dw_knowledge/contracts.py`,
  suy từ `_CLEARANCE_ALLOWS` (không gõ lại). `MemoryCandidate.classification` kiểm bằng
  `classification_rank`. Candidate khai **cao hơn** tài liệu thì giữ mức khai (chặt hơn
  là quyền của producer); khai thấp hơn thì `DomainError`, rollback, 0 item, 0 candidate.
  Tài liệu mang nhãn ngoài thang (`knowledge.documents.classification` chưa có CHECK)
  → `DomainError`, không đoán hạng.
- **Workspace**: `tables.visible_from_workspace` là một biểu thức cho "workspace này dùng
  được tài liệu kia" (của mình hoặc global); `gateway.list_documents`, `read_document`
  và `SqlEvidenceStore` cùng đọc nó (hai bản inline trong gateway đã thay). Lọc Qdrant
  của `search` là dạng khác (payload filter), không đổi.
- **Mục 4 (thử lại)**: `_deliver` của outbox xử lý `UndeliverableEventError` và lỗi khác
  **như nhau** (`record_failure`, thử lại tới `max_attempts`), nên không có vòng lặp vô
  hạn trước hay sau. Handler giờ ánh xạ `DomainError` từ `propose` sang
  `UndeliverableEventError` để lý do ghi lại đúng loại. Docstring của
  `UndeliverableEventError` hứa nhiều hơn những gì `_deliver` làm: ghi ở area Open,
  không sửa ở đây.
- **Retention**: gỡ `sensitive`, `ephemeral`, `legal_hold` (YAML `retention@1.5.0`, lý do
  ghi trong file). `dw_memory.service.RETENTION_CLASS` là đường gán duy nhất;
  `dw_worker.main.RETENTION_POLICY_PATH` là tệp đang ghim; test so bằng nhau hai chiều.
  `test_legal_hold_is_never_swept_however_old` vốn đã dùng chính sách giả, không phải
  chuyển. Bình luận nhắc tên tệp (`offboarding.py`, `spend_guard.py`,
  `test_restore_drill.py`, `test_worker.py`) trỏ sang 1.5.0; migration `c3ec03bd6fd1`
  không sửa.
- **Eval**: fixture `pf_memory_policy.json` khai `sources` thay `confidence`; dataset đổi
  nội dung nên lên `platform@1.1.0`.
- **Chưa kiểm khi REVIEW**: candidate REVIEW không qua `record` (như trước), nên bằng
  chứng và phân loại của nó chưa được kiểm lúc đề xuất. `memory-review-queue` phải gọi
  cùng `record` + phép so phân loại khi duyệt; ghi vào spec của lát đó.

Kiểm chứng (repo này, infra `dw_codebase`):

- Đỏ trước (unit): `uv run pytest packages/python/dw_memory/tests/unit/test_policy.py packages/python/dw_knowledge/tests/unit/test_knowledge_contracts.py apps/worker/tests/unit/test_memory_consumer.py apps/worker/tests/unit/test_worker.py`
  → 21 failed trước khi sửa code. Test integration mới được viết trước code nhưng chỉ chạy
  sau khi infra lên; đỏ của chúng được chứng minh bằng mutation bên dưới.
- Đo DB dev trước migration: `memory.items` và `memory.write_candidates` 0 dòng.
  `alembic upgrade head` → `downgrade -1` → `upgrade head`: 5 → 0 → 5 ràng buộc CHECK, head
  `a4104b95722b`.
- `uv run pytest packages/python/dw_memory/tests -m integration -k "not qdrant and not vector"`
  → 57 passed. `packages/python/dw_knowledge/tests -m integration -k "not qdrant"` → 31
  passed. `dw_platform` `test_rls_coverage.py test_privileges.py test_migration_and_rls.py`
  → 21 passed.
- `make ci` → exit 0: ruff, mypy (368 files), unit 914 passed / 3 skipped, import-linter
  5 kept / 0 broken, declared-deps 11, contract 2 passed, eval smoke
  `platform_smoke@1.1.0` 4/4, release manifest up to date.
- Qdrant ở máy này chậm (tạo một collection rỗng 8 chiều bằng `curl` mất 7,8 s, ba stack
  Docker cùng chạy), nên hai lượt đầu các test cần Qdrant lỗi `httpx.ReadTimeout`. Lượt
  cuối, không đổi code:
  `uv run pytest packages/python/dw_memory/tests packages/python/dw_knowledge/tests/integration/test_qdrant_tenant_filter.py apps/worker/tests -m integration`
  → exit 0, 80 passed, 0 failed, 0 error (pytest ở repo này không in dòng tổng kết; đếm
  dấu chấm).

Chứng minh đỏ (gỡ chốt, chạy, đỏ, hoàn nguyên byte-for-byte, `git diff --stat` như cũ):

| Chốt gỡ                                         | Test đỏ                                                                                                                     |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Bỏ phép so phân loại trong `propose`            | `test_a_candidate_claiming_less_than_its_evidence_is_refused_and_nothing_is_written`                                        |
| Evidence ghi `ref.classification`               | `test_evidence_records_the_documents_classification_not_the_references`                                                     |
| Bỏ `if not chunk.visible`                       | `test_a_chunk_from_another_workspace_is_refused_as_evidence`                                                                |
| `visible_from_workspace` bỏ nhánh global        | `test_a_global_document_from_another_workspace_can_be_cited`                                                                |
| Không bắt nhãn ngoài thang                      | `test_a_document_labelled_off_the_ladder_is_refused_not_ranked`                                                             |
| `MemoryCandidate` có lại `confidence`           | `test_a_payload_carrying_its_own_confidence_is_refused_not_obeyed`, `test_a_candidate_cannot_carry_its_own_confidence`      |
| Đếm ref thay vì tài liệu                        | `test_citing_one_document_twice_is_still_one_source`                                                                        |
| `auto_write_sources = 1`                        | `test_one_document_goes_to_review`                                                                                          |
| Bỏ validator phân loại                          | `test_a_classification_off_the_ladder_is_refused_at_parse`                                                                  |
| Payload nhận cả `1.0`                           | `test_a_schema_1_0_payload_is_refused_rather_than_guessed_at`                                                               |
| Handler không ánh xạ `DomainError`              | `test_evidence_the_service_refuses_is_undeliverable_not_retried`                                                            |
| Bỏ CHECK `items.memory_type` khỏi migration     | `test_a_value_off_the_set_is_refused_by_name[items-memory_type-...]`, `test_the_checks_hold_exactly_the_sets_the_code_owns` |
| Thêm `MemoryType.DECISION` không thêm vào CHECK | `test_the_checks_hold_exactly_the_sets_the_code_owns`                                                                       |
| Khôi phục lớp `sensitive`                       | `test_the_pinned_retention_policy_promises_only_classes_code_can_assign`                                                    |

Thêm từ `reviewing-feature-security` (§1, §6): test chunk của tài liệu global **tenant
khác** bị từ chối (truy vấn evidence giờ join `knowledge.documents`, mà RLS của nó cho
mọi tenant đọc tài liệu global; chunk vẫn chỉ RLS tenant), và test nhãn ngoài thang ở
trên.
