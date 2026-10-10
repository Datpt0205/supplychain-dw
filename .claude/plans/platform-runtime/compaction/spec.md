# Compaction thật sự nén; checkpoint không phình vô hạn

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm. Nguồn: audit harness 6/10/2026 (khu
`context-compaction`), kiểm lại trên `main` (`4cb45dc`) của repo này. `uv.lock` ghim
`langchain 1.4.1`, cùng bản audit đã đo.

## Hiện trạng (đã kiểm trong code; phần "đo" là của audit, phải đo lại)

- `PlatformSummarizationMiddleware.__init__`
  (`packages/python/dw_agent_runtime/src/dw_agent_runtime/adapters/context_compaction.py:91-128`)
  gọi `super().__init__` không truyền `trim_tokens_to_summarize`, nên dùng mặc định
  4000 của thư viện (`langchain/agents/middleware/summarization.py`,
  `_DEFAULT_TRIM_TOKEN_LIMIT`). `_summarise` (`:221-240`) gọi
  `_trim_messages_for_summary`, cắt `strategy='last', start_on='human'`.
- **Vòng tool dài trong một lượt (đo bởi audit):** đoạn bị gỡ là [summary cũ, câu hỏi
  của người, 12 × (AIMessage gọi tool + ToolMessage ~1000 token)] ≈ 12.8k token; trim
  trả `[]` vì HumanMessage duy nhất nằm ngoài 4000 token cuối. `abefore_model` gặp
  `summarised is None` và trả `None` (`:172-176`): không nén gì, context lớn tới khi nhà
  cung cấp từ chối. Test duy nhất của nhánh này
  (`test_history_too_large_to_summarise_is_not_traded_for_a_placeholder`, đặt
  `trim_tokens_to_summarize = 1`) ghim việc "không làm gì" là đúng.
- **Thread nhiều lượt (đo bởi audit):** đoạn bị gỡ [SUMMARY cũ, 20 × (human 1000 ký tự,
  AI 3000 ký tự)] ≈ 20.7k token; trim giữ h17–a19. Summary cũ và h0–h16 không tới bộ tóm
  tắt, nên mỗi lần nén làm rơi những gì lần trước đã giữ. Audit digest vẫn phủ cả đoạn bị
  gỡ, nên vết ghi nói "đã nén" trong khi nội dung mất im lặng.
- **Docstring sai:** module test (`tests/unit/test_context_compaction.py:5-8`) nói "a
  history too long for one pass is still compacted" được ghim; không test nào làm vậy.
  `_record` (`context_compaction.py:252-258`) nói "the text itself is gone from the
  checkpoint after this"; các checkpoint trước vẫn giữ nguyên văn.
- **Ngân sách đầu vào không có chủ:** 4000 là hằng của thư viện. `ModelRoute`
  (`model/profiles.py:28-50`) không có trường cửa sổ hay ngân sách đầu vào.
- **Checkpoint không bao giờ bị xóa:** `SqlAlchemyCheckpointSaver.aput` ghi toàn bộ
  danh sách message mỗi super-step; `adelete_thread` raise `NotImplementedError`
  (`adapters/checkpoint.py:241-244`). Không lane retention nào chạm
  `platform.run_checkpoints` hay `run_checkpoint_writes`.
- **Offboarding ĐÃ xóa checkpoint** (audit nói không; sai ở repo này): lane tìm bảng từ
  catalog theo policy `tenant_isolation_%` và quyền DELETE của `dw_app`
  (`dw_platform/adapters/persistence/offboarding.py`), và hai bảng checkpoint có cả hai
  (`0001_platform_baseline.sql:863-866`, `0001_platform_grants.sql`). Lát này chỉ ghim
  điều đó bằng test, không xây lại.

## Mục tiêu

1. Trong một vòng tool dài, compaction tóm tắt thật (không còn trim rỗng khi
   HumanMessage duy nhất đã cũ).
2. Trên thread nhiều lượt, summary hệ thống sinh ra lần trước (`dw_system_generated`)
   luôn được đưa vào bộ tóm tắt như mỏ neo để cập nhật, không suy lại; phần cũ hơn không
   rơi im lặng.
3. Đầu vào bộ tóm tắt bị chặn bằng một ngân sách tường minh lấy từ model profile của bộ
   tóm tắt, không phải 4000 cứng.
4. Hai docstring sai được sửa (hoặc có test đúng như chúng nói).
5. Checkpoint của run đã xong bị tỉa theo một thời hạn có version; offboarding được ghim
   bằng test là xóa chúng.

## Quy tắc và quyết định

- **Ngân sách có một chủ:** trường tùy chọn mới trên `ModelRoute`, ví dụ
  `max_input_tokens`, đọc bởi compaction ngay trong lát này (không khai trường không ai
  đọc, `failure-modes.md` #1). Route chưa khai thì compaction từ chối dựng (ConfigError
  khi build), không âm thầm lấy 4000.
- **Vượt ngân sách thì tóm tắt từng khúc:** đoạn bị gỡ lớn hơn ngân sách được tóm tắt
  lặp, mỗi khúc cập nhật mỏ neo; không cắt bỏ phần đầu. Trim không còn đòi
  `start_on='human'`. Vẫn giữ: không bao giờ đổi lịch sử lấy câu placeholder; một
  ToolMessage và AIMessage gọi nó không bị tách (approval đang chờ sống sót, test hiện có).
- **Mỗi lần gọi bộ tóm tắt đều qua trần chi tiêu** (`_budget.check`/`record` như hiện
  nay), kể cả các khúc.
- **Tỉa checkpoint:** chỉ thread không còn run nào đang chạy hay chờ duyệt
  (`worker_runs.status` ngoài `completed/failed/cancelled`). Hai thời hạn trong
  `configs/policies/retention@<bản kế>.yaml`: checkpoint không phải mới nhất của thread
  quá N ngày thì xóa (mới nhất giữ để thread tiếp tục được); cả thread im quá M ngày thì
  xóa hết. Đề xuất N = 7, M = 730 (bằng lớp memory `default`); giá trị là quyết định của
  Đạt, ghi vào "Decisions Đạt owes" nếu chưa chốt, và lane chỉ xóa khi có số.

## Trong phạm vi

- `context_compaction.py`, `agent_factory.py` (`CompactionSpec`), `model/profiles.py`,
  `configs/models/*.yaml` (route tóm tắt khai ngân sách), `configs/runtime` copy của prompt
  tóm tắt nếu cần câu "cập nhật mỏ neo" (bản copy mới, có version).
- `adapters/checkpoint.py` hoặc một module retention cạnh nó; migration mới (policy
  `worker_drain` cho hai bảng checkpoint như `0010_memory_retention_drain.py`, index phục
  vụ lượt quét); `retention_policy.py`; lane `retention` của worker.

## Ngoài phạm vi

- Xóa kết quả tool cũ (`ContextEditingMiddleware`): làm cùng agent loop dài đầu tiên.
- Trường `context_window` cho trigger dạng phân số: chỉ khi một worker dùng.
- Eval giữ mục tiêu qua compaction: thuộc worker đầu tiên bật compaction.

## Quy tắc và kiểm soát

| Chốt                              | Test đỏ khi gỡ                                                                                                     |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Vòng tool dài được nén            | Khôi phục trim `start_on='human'` / 4000 → test "một lượt, 12 tool call, vượt trigger, có summary" đỏ              |
| Summary cũ là mỏ neo              | Bỏ bước đưa summary cũ vào → test "nội dung chỉ có ở summary cũ còn trong đầu vào bộ tóm tắt" đỏ                   |
| Ngân sách từ profile              | Đổi ngân sách trong profile → số token đầu vào mỗi lần gọi đổi theo; route thiếu trường → ConfigError              |
| Không đổi lịch sử lấy placeholder | test hiện có giữ xanh, đổi sang điều kiện thật (bộ tóm tắt lỗi), không còn `trim_tokens_to_summarize = 1`          |
| Trần chi tiêu tính mọi khúc       | Bỏ `record` cho khúc → test ledger đỏ                                                                              |
| Tỉa checkpoint                    | Bỏ lane → checkpoint cũ của thread đã xong còn, test đỏ; thread có run `waiting_approval` không mất checkpoint nào |
| Offboarding xóa checkpoint        | Thu DELETE của `dw_app` trên `run_checkpoints` trong test → test offboarding đỏ                                    |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved`, chứng minh đỏ dưới `## Comments`, kèm số đo lại của
  hai trường hợp audit trên bản ghim (trước và sau).
- `make ci` xanh; integration `dw_agent_runtime`, `dw_platform`, `apps/worker` xanh.
- Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- Không chờ lát nào. Nếu `memory-write-trust` đã nâng bản retention, lát này nâng bản kế.

## Câu hỏi còn mở

1. ~~N và M (đề xuất 7 và 730 ngày).~~ Ship 7 và 730 trong `retention@1.6.0.yaml`
   (2026-10-06, theo ủy quyền tự quyết); đổi một dòng là đủ.

## Danh sách ticket

| #   | Ticket                                                                                                                             | Status   | Blocked by |
| --- | ---------------------------------------------------------------------------------------------------------------------------------- | -------- | ---------- |
| 01  | [Compaction nén được vòng tool dài và giữ mỏ neo; checkpoint có thời hạn](issues/01-compaction-anchor-and-checkpoint-retention.md) | resolved | —          |
