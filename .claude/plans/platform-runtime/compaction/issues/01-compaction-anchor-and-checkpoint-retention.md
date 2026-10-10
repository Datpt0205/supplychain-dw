# 01 — Compaction nén được vòng tool dài và giữ mỏ neo; checkpoint có thời hạn

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Compaction làm đúng việc nó tồn tại để làm (một vòng tool dài, một thread nhiều lượt),
đầu vào bộ tóm tắt có ngân sách do profile sở hữu, và checkpoint của run đã xong không
nằm mãi. Spec, mục "Quy tắc và quyết định" và "Quy tắc và kiểm soát".

## Việc cần làm

1. **Đo trước** (`failure-modes.md` #4): dựng lại hai trường hợp của audit với
   `langchain` đang ghim (không gọi model; bộ tóm tắt giả ghi lại đầu vào). Ghi số vào
   `## Comments`. Đây cũng là hai test đầu tiên, và phải đỏ trước khi sửa.
2. `model/profiles.py`: `ModelRoute.max_input_tokens: int | None` (gt=0). Route tóm tắt
   trong `configs/models/*.yaml` khai giá trị. `CompactionSpec`/`platform_middleware`
   đọc nó qua `ModelProfileRegistry.resolve(summary_profile_id, tenant_id=...)`;
   thiếu → `ConfigError` khi build.
3. `context_compaction.py`:
    - Không dùng `_trim_messages_for_summary` của thư viện cho đầu vào bộ tóm tắt. Chia
      đoạn bị gỡ thành các khúc vừa ngân sách (đếm bằng `token_counter` của middleware),
      không tách một AIMessage gọi tool khỏi ToolMessage của nó, không đòi khúc bắt đầu
      bằng HumanMessage.
    - Tìm summary hệ thống lần trước (`additional_kwargs["dw_system_generated"]`) trong
      đoạn bị gỡ; đưa nó vào lời gọi đầu tiên như mỏ neo, prompt bảo "cập nhật bản này",
      và mỗi khúc sau nhận summary của khúc trước làm mỏ neo. Nếu prompt tóm tắt cần câu
      mới thì ra bản copy runtime mới có version; không sửa bản cũ tại chỗ.
    - Mỗi lời gọi: `_budget.check` trước, `_budget.record` sau.
    - Một khúc lỗi → bỏ cả lần nén, lịch sử giữ nguyên (như hiện nay).
    - Sửa docstring `_record`: digest chứng minh phần bị gỡ; các checkpoint trước vẫn giữ
      nguyên văn cho tới khi lane tỉa xóa chúng.
4. `tests/unit/test_context_compaction.py`: sửa docstring module cho khớp test có thật;
   thêm test "lịch sử quá dài cho một lần vẫn được nén" đúng như nó nói.
   `test_history_too_large_to_summarise_is_not_traded_for_a_placeholder` chuyển sang điều
   kiện thật còn lại.
5. **Tỉa checkpoint:**
    - Migration mới (`uv run alembic revision -m "..."`): policy `worker_drain_*` cho
      `platform.run_checkpoints` và `run_checkpoint_writes` theo mẫu
      `0010_memory_retention_drain.py`; index phục vụ lượt quét (dẫn đầu bằng cột lượt
      quét lọc, xem `CLAUDE.md` "Data model rules"). Grants đi cùng migration;
      `test_rls_coverage.py`, `test_privileges.py` xanh.
    - `retention_policy.py` + `configs/policies/retention@<bản kế>.yaml`: khối
      `checkpoints` (`superseded_days`, `idle_thread_days`); đổi mọi chỗ ghim tên tệp.
    - Một class retention (cạnh `checkpoint.py`, theo kiểu `SqlMemoryRetention`): lô có
      giới hạn, chỉ thread không có run ở trạng thái khác `completed/failed/cancelled`;
      xóa writes rồi checkpoints; nối vào lane `retention` ở `apps/worker/src/dw_worker/main.py`.
    - `adelete_thread`: giữ raise, sửa thông điệp trỏ tới lane.
6. Test offboarding: một tenant có checkpoint; sau `purge_rows` còn 0 dòng ở cả hai bảng.

## Tiêu chí chấp nhận

- Hai test đo của bước 1 đỏ trên code cũ, xanh sau sửa; số đo trước/sau trong Comments.
- Test: ngân sách 2000 so với 8000 cho số token đầu vào mỗi lời gọi khác nhau tương ứng;
  route thiếu `max_input_tokens` → `ConfigError`.
- Test: approval đang chờ vẫn sống qua compaction (test hiện có xanh); ledger có một
  mục cho mỗi khúc.
- Integration: thread đã xong có 5 checkpoint cũ → còn đúng checkpoint mới nhất; thread
  im quá hạn → 0; thread có run `waiting_approval` → không mất gì; tenant B không bị
  chạm khi sweep chạy cho dữ liệu của A (lượt quét chéo tenant chỉ xóa theo điều kiện).
- Chứng minh đỏ cho từng dòng bảng "Quy tắc và kiểm soát", ghi dưới `## Comments`.
- `make ci` xanh; `make infra-down` cho repo này khi xong.

## Nguồn

- Audit harness 6/10/2026, khu `context-compaction`: gap no-op trong vòng tool, gap mất
  summary trên thread nhiều lượt, gap không có chủ cửa sổ, gap checkpoint phình
  (phần "offboarding/retention never touch the table" sai một nửa ở repo này: offboarding
  có xóa, qua catalog).
- `context_compaction.py:88-296`, `agent_factory.py:84-106`, `model/profiles.py:28-58`,
  `adapters/checkpoint.py:128-244`, `0001_platform_baseline.sql:366-398, 863-866`,
  `dw_platform/adapters/persistence/offboarding.py`, `dw_memory/retention.py`,
  `db/migrations/versions/0010_memory_retention_drain.py`.
- `failure-modes.md` #1, #3, #4, #6.

## Comments

### 2026-10-06 — đã làm (nhánh `feat/platform-hardening`)

**Đo trước, trên `langchain 1.4.1` đang ghim** (bộ tóm tắt giả ghi lại đầu vào,
`keep=("messages", 4)`, script đo trong scratchpad của phiên):

| Trường hợp                                                                              | Trước                                                                         | Sau (ngân sách 8000)                                                       | Sau (ngân sách 2000)       |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------- | -------------------------- |
| Vòng tool: gỡ 26 message ≈ 12442 token (summary cũ, 1 câu hỏi, 12 cặp tool ~1000 token) | 0 lời gọi, 0 audit, không nén                                                 | 2 lời gọi [7683, 5549] token, 12/12 kết quả tool tới bộ tóm tắt, có mỏ neo | 12 lời gọi, mỗi lời ≤ 1497 |
| Thread: gỡ 41 message ≈ 20255 token (summary cũ, 20 lượt)                               | 1 lời gọi 3357 token, mỏ neo KHÔNG tới, 3/20 lượt tới, audit vẫn ghi "đã nén" | 3 lời gọi [7820, 7500, 6226], có mỏ neo, 20/20 lượt                        | 20 lời gọi, mỗi lời ≤ 1729 |

Hai test đo (`test_a_long_tool_loop_inside_one_turn_is_compacted`,
`test_the_previous_summary_reaches_the_summariser_as_its_anchor`) đỏ trên code cũ
(lần lượt "the summariser was never asked" và `MÃ-NEO-7731` không có trong đầu vào),
xanh sau sửa.

**Đã thay đổi:**

- `ModelRoute.max_input_tokens` (gt=0); `gateway` và `luna` khai 32000 trên route
  `chat` (route mà chi phí bộ tóm tắt được tính theo). Thiếu → `ConfigError` khi build
  (lớp platform) và khi chạy (route của tenant).
- `context_compaction.py`: không còn `_trim_messages_for_summary`
  (`trim_tokens_to_summarize=None`). Phần bị gỡ chia thành đơn vị (AIMessage gọi tool
  cùng các ToolMessage của nó), gom thành khúc vừa ngân sách, đếm bằng `token_counter`
  của middleware. Summary hệ thống lần trước là mỏ neo của lời gọi đầu
  (`context_summary_update_prompt`, copy `runtime@1.6.0`, bản mới; 1.4.0/1.5.0 bị từ
  chối), summary của khúc trước là mỏ neo của khúc sau. `check` trước mỗi lời gọi,
  `record` sau mỗi lời gọi. Một khúc lỗi → bỏ cả lần nén. Một message lớn hơn cả ngân
  sách → không nén, không gọi model (điều kiện thật còn lại của test placeholder).
  Audit thêm `summary_calls`. Docstring module (cả file test) và `_record` sửa cho đúng.
- `apps/api` nạp `runtime@1.6.0.yaml`.
- Migration `7bbd071748ca` (sinh bằng `alembic revision`): `worker_drain_*` cho
  `run_checkpoints`, `run_checkpoint_writes`; `worker_drain_worker_runs` **FOR SELECT**
  (lượt quét phải thấy run mới biết thread còn chờ không; spec không nêu, nhưng thiếu
  nó thì RLS giấu run); `ix_run_checkpoints_created_at (created_at)`. Không cần grant mới.
- `retention@1.6.0.yaml` (thay 1.5.0, đổi mọi chỗ ghim): `checkpoints:
superseded_days: 7, idle_thread_days: 730` (số đề xuất của ticket, chọn theo ủy quyền
  "tự quyết hướng tốt nhất"; đổi một dòng là đủ). `CheckpointRetention` từ chối
  idle < superseded.
- `SqlCheckpointRetention` (`adapters/checkpoint_retention.py`): một lô `batch_limit`,
  cũ nhất trước; xóa writes rồi checkpoints. Điều kiện viết **dương**: thread phải có
  ít nhất một run đã kết thúc nhìn thấy được VÀ không run nào chưa kết thúc, nên mất
  policy drain trên `worker_runs` làm lượt quét dừng chứ không xóa trạng thái của run
  đang chờ duyệt. Cái giá: thread có checkpoint mà không có dòng run nào không bị tỉa ở
  đây (offboarding vẫn xóa).
- Worker: lane riêng `checkpoint_retention`, không gộp vào lane `retention` (ticket ghi
  `retention`, nhưng quy ước trong `main.py` là mỗi lượt quét một lane để một lượt lỗi
  không kéo lượt khác).
- `adelete_thread` vẫn raise, thông điệp trỏ tới lane và offboarding.
- Offboarding: `test_purge_deletes_the_tenants_run_checkpoints` ghim việc xóa cả hai
  bảng, tenant khác giữ nguyên.

**Chứng minh đỏ** (script đột biến: sửa, chạy test đích, khôi phục, so sha256; cả 25
đều đỏ và `restored=True`):

| Chốt                                | Đột biến                               | Kết quả                                       |
| ----------------------------------- | -------------------------------------- | --------------------------------------------- |
| Vòng tool được nén                  | trim thư viện 4000/`start_on='human'`  | đỏ                                            |
| Summary cũ là mỏ neo                | không truyền summary cũ                | đỏ                                            |
| Mỏ neo qua các khúc                 | khúc sau không nhận summary khúc trước | đỏ                                            |
| Ngân sách từ profile                | `budget = 4000`                        | đỏ                                            |
| Route thiếu trường → ConfigError    | bỏ kiểm khi build                      | đỏ                                            |
| Copy phải có update prompt          | bỏ điều kiện                           | đỏ (1.5.0)                                    |
| Không placeholder                   | bỏ kiểm message quá cỡ                 | đỏ (đã gọi model)                             |
| Ledger mỗi khúc                     | bỏ `record`                            | đỏ                                            |
| Trần kiểm mỗi khúc                  | chỉ kiểm trước khúc đầu                | đỏ                                            |
| Khúc lỗi bỏ cả lần nén              | `break` thay `return None`             | đỏ                                            |
| Không tách tool call/kết quả        | bỏ gom đơn vị                          | đỏ                                            |
| Lane có đăng ký                     | đổi tên lane                           | đỏ (`test_worker`)                            |
| Thread có run đang chờ không mất gì | bỏ điều kiện run chưa xong             | đỏ                                            |
| Writes đi cùng checkpoint           | chỉ xóa checkpoints                    | đỏ                                            |
| Giữ checkpoint mới nhất             | `>=` thay `>`                          | đỏ                                            |
| Thread im quá hạn → 0               | bỏ nhánh idle                          | đỏ                                            |
| Trong hạn thì giữ                   | bỏ điều kiện tuổi                      | đỏ                                            |
| Lô có giới hạn                      | bỏ `.limit`                            | đỏ                                            |
| Fail closed                         | bỏ điều kiện dương                     | đỏ (thread không có run bị xóa)               |
| Policy drain `worker_runs`          | bỏ policy                              | đỏ (không tỉa gì); test run đang chờ vẫn xanh |
| Bỏ policy VÀ điều kiện dương        | cả hai                                 | đỏ (run đang chờ mất checkpoint)              |
| Policy drain checkpoint             | bỏ                                     | đỏ                                            |
| Offboarding xóa checkpoint          | `REVOKE DELETE ... FROM dw_app`        | đỏ                                            |
| idle ≥ superseded                   | bỏ validator                           | đỏ                                            |

**Lệnh và số:**

- `make ci`: xanh — lint, mypy (371 file), 939 unit passed / 3 skipped, kiến trúc
  (5 contract kept, 11 package), contract 2 passed, eval-smoke `platform_smoke@1.1.0`
  4/4, release-manifest OK (`sha256:fcd0d4de51c4…`, sinh lại, LF).
- `uv run pytest -m integration packages/python/dw_agent_runtime
packages/python/dw_platform apps/worker packages/python/dw_memory
packages/python/dw_knowledge`: 344 passed (gồm `test_rls_coverage`,
  `test_privileges`). Lần chạy đầu đỏ `test_checkpoints_are_tenant_isolated` vì test
  mới ghi dưới `TENANT_B` trong DB chung của phiên; giờ mỗi thread một tenant riêng.
- `make infra-up` trước, `make infra-down` sau (chỉ stack `dw_codebase`).
- `reviewing-feature-security`: cô lập tenant (policy drain chỉ code worker bật, test
  chéo tenant, `test_rls_coverage`); approval (run đang chờ giữ checkpoint; approval
  đang chờ sống qua compaction); nội dung không tin cậy (mỏ neo vào prompt 1.6.0 như
  DỮ LIỆU, summary vẫn đóng khung hệ thống); vòng đời (chính lát này; kiểm trước mỗi
  lời gọi; fail closed). Authorization và provenance: không có route hay quyết định
  mới, không chạm audit/evidence.

**Còn lại, không chặn:** chưa có eval prompt-injection riêng cho mỏ neo compaction
(spec để eval cho worker đầu tiên bật compaction); thread không có dòng run nào chỉ
được offboarding dọn.
