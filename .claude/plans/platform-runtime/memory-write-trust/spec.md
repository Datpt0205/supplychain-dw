# Ghi memory chỉ tin điều code đã kiểm: phân loại theo bằng chứng, độ tin do workflow tính

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm. Nguồn: audit harness 6/10/2026 (khu
`memory-store`), kiểm lại trên `main` (`4cb45dc`) của repo này.

## Hiện trạng (đã kiểm trong code)

- **Phân loại do candidate tự khai.** `MemoryService.propose` chép
  `candidate.classification` vào item (`dw_memory/service.py:122, 152`).
  `MemoryCandidate.classification: str = "internal"` (`dw_memory/policy.py:31`), không
  kiểm giá trị. Recall lọc theo phân loại đó (`service.py:434-442`), nên một candidate
  khai `internal` mà trích một chunk của tài liệu `confidential` được recall cho người
  chỉ có clearance `internal`.
- **Bằng chứng không kiểm workspace, không đọc phân loại.**
  `SqlEvidenceStore.record` (`packages/python/dw_knowledge/src/dw_knowledge/adapters/evidence_store.py:55-125`)
  chỉ kiểm chunk tồn tại (dưới RLS tenant), hash và document id. Cột `workspace_id` và
  `classification` của `knowledge.evidence` ghi thẳng từ input.
  `knowledge.chunks` không có cột phân loại; phân loại ở `knowledge.documents`
  (`dw_knowledge/tables.py:24`). Tài liệu `scope = 'global'` đọc được từ mọi tenant
  (`tables.py:38-40`, `gateway.py:436-440, 517-518`): tìm kiếm chấp nhận "workspace của
  người gọi HOẶC global", nên kiểm workspace phải theo cùng luật đó.
- **Ngưỡng AUTO_WRITE so một con số candidate mang tới.** `MemoryWritePolicy.evaluate`
  (`policy.py:45-60`) so `candidate.confidence` với 0.80/0.50. Payload outbox
  (`apps/worker/src/dw_worker/consumers/memory.py:87-105`) mang nguyên `MemoryCandidate`,
  gồm `confidence`. Không gì nói ai được đặt nó; nếu producer chép từ đầu ra model thì
  model quyết định điều gì được nhớ mà không cần người (CLAUDE.md "Agent and tool
  rules", hướng dẫn §6 của Đạt).
- **Cột tập giá trị cố định không có CHECK.** `memory.items.memory_type`,
  `classification`; `memory.write_candidates.decision`, `memory_type`,
  `classification` (`db/migrations/sql/0001_platform_baseline.sql:97-139`). `0007` chỉ
  thêm `ck_items_provenance_refs` (`failure-modes.md` #7).
- **Lớp retention khác `default` không gán được.** `service.py:124` ghi cứng
  `retention_policy="default"`; `MemoryCandidate` không có trường retention.
  `configs/policies/retention@1.4.0.yaml` khai `sensitive` (365 ngày), `ephemeral`
  (30), `legal_hold` (không hết hạn): ba lớp không dòng nào mang được
  (`failure-modes.md` #1).

## Mục tiêu

1. Memory lưu ở phân loại chặt nhất trong các tài liệu nó trích; candidate khai thấp
   hơn thì bị từ chối (không tự nâng: xem Quy tắc).
2. Bằng chứng từ workspace khác (trừ tài liệu global) bị từ chối; `knowledge.evidence`
   ghi phân loại của tài liệu, không của input.
3. Độ tin quyết định AUTO_WRITE do code workflow tính từ tín hiệu đã kiểm, không bao
   giờ chép từ đầu ra model; port, `MemoryCandidate` và payload nói rõ và cưỡng chế.
4. CHECK cho các cột tập cố định của `memory.items` và `memory.write_candidates`, trong
   một revision alembic mới (id hex ngẫu nhiên).
5. Ba lớp retention không gán được bị gỡ khỏi phần memory của chính sách retention.

## Quy tắc và quyết định

- **Từ chối, không tự nâng phân loại.** Policy quyết định trước khi bằng chứng được
  kiểm, và luật "restricted luôn cần người duyệt" (`policy.py:52-55`) đọc phân loại
  candidate khai. Nâng phân loại sau đó sẽ AUTO_WRITE một memory restricted, đi vòng
  qua chính luật ấy. Từ chối giữ luật đứng yên; producer phải khai đúng (nó có
  `EvidenceRef.classification` từ kết quả tìm kiếm). Nếu muốn nâng thay vì từ chối,
  phân loại phải được tính TRƯỚC `policy.evaluate`; đó là thay đổi lớn hơn, ghi lại
  nếu chọn.
- **Gỡ ba lớp retention thay vì làm chúng gán được.** Gán được nghĩa là phải quyết một
  ánh xạ (phân loại hay loại memory → lớp) mà chưa ai quyết, cho một đường ghi chưa có
  producer; còn `legal_hold` là quyết định của người và chưa có route nào để người đặt
  nó. Gỡ là thay đổi nhỏ hơn và trung thực: chính sách chỉ hứa điều code làm. Ghi trong
  YAML rằng `legal_hold` quay lại cùng route đặt nó. Hành vi "lớp không có `days` không
  bao giờ bị quét" vẫn được test bằng chính sách giả trong test.
- **Không CHECK cho `retention_policy`.** Tập giá trị của nó thuộc YAML có version; một
  CHECK là bản sao thứ hai (`failure-modes.md` #2). Dòng mang lớp lạ đã được giữ và đếm
  (`dw_platform/retention_policy.py`, "An unknown class is kept").
- **CHECK là bản sao của enum trong code**, nên phải có test so CHECK đọc từ catalog với
  `MemoryType`, `WriteDecision` và các khóa của thang clearance
  (`dw_knowledge/contracts.py:23-37`): hai bên lệch thì đỏ to.

## Trong phạm vi

- `dw_knowledge/adapters/evidence_store.py`, `dw_memory/ports.py`, `dw_memory/policy.py`,
  `dw_memory/service.py`, `apps/worker/src/dw_worker/consumers/memory.py`,
  `dw_evals/graders.py` (chỗ dựng `MemoryCandidate` duy nhất ngoài test).
- Revision alembic mới; `configs/policies/retention@<bản kế>.yaml` và mọi chỗ ghim tên
  tệp (`rg "retention@1.4.0"`: `apps/worker/src/dw_worker/main.py`, test, migration
  `c3ec03bd6fd1` chỉ là chú thích lịch sử, không sửa); release manifest.

## Ngoài phạm vi

- Bằng chứng không phải chunk knowledge (bản ghi nghiệp vụ, tin nhắn): chờ context đầu
  tiên cần, qua một Protocol do consumer khai (audit, gap medium, real chưa xác nhận).
- CHECK cho cột phân loại của `knowledge.*`: cùng dạng lỗi, ghi vào Open của area nếu
  lát này xác nhận.
- Hàng đợi REVIEW: lát `memory-review-queue`.

## Quy tắc và kiểm soát

| Chốt                                               | Test đỏ khi gỡ                                                                                                                  |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Từ chối candidate khai phân loại thấp hơn tài liệu | Bỏ phép so → candidate `internal` trích tài liệu `confidential` ghi được item, test đỏ                                          |
| Phân loại evidence lấy từ tài liệu                 | Ghi lại `ref.classification` → test đọc `knowledge.evidence.classification` đỏ                                                  |
| Từ chối chunk của workspace khác                   | Bỏ phép kiểm → chunk workspace W2 làm bằng chứng cho memory ở W1, test đỏ                                                       |
| Tài liệu global vẫn trích được                     | Kiểm workspace quá chặt (bỏ nhánh global) → test trích tài liệu global đỏ                                                       |
| Độ tin từ model không quyết AUTO_WRITE             | Cho payload mang lại `confidence` thô → test "confidence 1.0 bị tiêm không auto-write" đỏ                                       |
| CHECK cột tập cố định                              | Bỏ CHECK → test chèn `memory_type='bogus'` (vai migrator) đỏ; thêm một giá trị vào enum mà không vào CHECK → test so catalog đỏ |
| Retention chỉ hứa lớp gán được                     | Khôi phục `sensitive` → test "mọi lớp memory trong chính sách đều có đường gán" đỏ                                              |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved`, chứng minh đỏ dưới `## Comments`.
- `uv run pytest packages/python/dw_memory packages/python/dw_knowledge apps/worker -m integration`
  và `make ci` xanh; `alembic upgrade head` rồi `downgrade -1` rồi `upgrade head` sạch.
- Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- Sau `memory-vectors` (cùng `service.py`). `memory-review-queue` dựa trên đường ghi đã
  kiểm của lát này và mở rộng CHECK của `decision` nếu thêm giá trị.

## Câu hỏi còn mở

1. Tín hiệu đã kiểm nào làm nên độ tin? Đề xuất tối thiểu: số `EvidenceRef` đã qua
   `SqlEvidenceStore` (đếm lại trong service, không tin số producer khai) và một cờ
   "người đã xác nhận" chỉ đặt được từ quyết định approval (lát `memory-review-queue`).
   Ngưỡng nằm trong `MemoryWritePolicy` có version, không trong payload.

## Danh sách ticket

| #   | Ticket                                                                                                                 | Status   | Blocked by        |
| --- | ---------------------------------------------------------------------------------------------------------------------- | -------- | ----------------- |
| 01  | [Phân loại theo bằng chứng, độ tin do workflow tính, CHECK, lớp retention trung thực](issues/01-memory-write-trust.md) | resolved | memory-vectors 01 |
