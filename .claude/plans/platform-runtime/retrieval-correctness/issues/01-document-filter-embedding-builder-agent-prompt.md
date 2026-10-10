# 01 — Lọc tài liệu trong Qdrant, một builder embedding, prompt agent loop có version

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Ba sửa nhỏ, mỗi cái một chủ cho một sự thật: tài liệu nào được hỏi (trong bộ lọc
Qdrant), model nào embed (một builder), prompt nào agent loop dùng (registry). Spec, mục
"Quy tắc và quyết định" và "Quy tắc và kiểm soát".

## Việc cần làm

1. **Lọc tài liệu trước top-k**
    - `dw_knowledge/gateway.py` `search`: truyền `query.document_ids` xuống index;
      gỡ dòng lọc sau (`gateway.py:581-582`).
    - `adapters/qdrant_index.py` `search`: tham số `document_ids: Sequence[UUID] = ()`;
      không rỗng thì thêm
      `FieldCondition(key="source_document_id", match=MatchAny(any=[...]))` vào `must`, cạnh các điều kiện của `trusted_filter`.
      Cập nhật `VectorIndexPort` và mọi fake.
    - Test tích hợp (Qdrant thật): 30 chunk của tài liệu khác giống câu hỏi hơn 5 chunk
      của tài liệu D; hỏi `document_ids=[D]`, `top_k=5` → đủ 5 chunk, đều của D.
    - `test_qdrant_tenant_filter.py`: biến thể có `document_ids` của tenant B gọi dưới
      tenant A → 0 kết quả.
2. **Một builder embedding**
    - Một hàm dùng chung (đặt ở chỗ cả api lẫn worker đã phụ thuộc; kiểm
      `pyproject.toml` import-linter trước khi chọn) nhận provider, route embedding đã
      resolve, base URL, key; trả `EmbeddingPort`; giữ `ConfigError` cho provider lạ và
      route thiếu `dimensions`.
    - `apps/api/.../bootstrap/knowledge.py` và `apps/worker/.../composition.py` gọi nó;
      xóa thân hàm cũ. Sửa docstring worker; xóa chuỗi docstring chết.
    - Test: cả hai app, cùng settings, dựng adapter cùng `model` và chiều; provider lạ →
      `ConfigError` ở cả hai.
3. **Prompt agent loop có version** (chỉ khi gọn như spec mô tả; nếu không, theo Câu hỏi 1
   của spec)
    - `AgentSpec`: bỏ `render_prompt`; thêm `prompt_id`, `prompt_version`,
      `prompts: PromptRegistry`,
      `prompt_variables: Callable[[ModelRequest], dict[str, str]]` (biến theo từng lần gọi: ngày, màn hình đang xem).
    - `WorkerSystemPrompt`: render bằng
      `prompts.render(id, version, variables, tenant_id=<RunContext của request>)`.
    - Runner: ghi chi phí agent loop bằng prompt id/version đã pin (từ
      `WorkerDefinition` nếu chọn phương án đề xuất); xóa `AGENT_LOOP_PROMPT_VERSION`.
    - `scripts/release_manifest.py`: prompt đó vào manifest như mọi prompt khác.
    - Sửa test `test_agent_factory.py` và các test dựng `AgentSpec`.

## Tiêu chí chấp nhận

- Các test ở trên, cộng:
    - ledger của một run agent ghi `prompt_id`/`prompt_version` của registry, không
      `0.0.0`;
    - tenant A có bản prompt riêng: run của A render bản đó, run của B render bản
      platform (fallback không sang ngang);
    - prompt không có trong registry → lỗi khi build agent.
- Chứng minh đỏ cho từng dòng bảng "Quy tắc và kiểm soát" của spec, ghi dưới
  `## Comments`.
- `rg "AGENT_LOOP_PROMPT_VERSION|render_prompt" packages apps` không còn kết quả (trừ
  ghi chú lịch sử nếu có).
- `make ci` xanh; `make infra-down` cho repo này khi xong.

## Nguồn

- Audit harness 6/10/2026: khu `retrieval` gap "document_ids is applied after top-k";
  khu `memory-store` gap "Two embedding builders" (real=true); khu
  `file-memory-and-instructions` gap "The agent-loop system prompt is not a versioned
  artifact".
- `gateway.py:575-610`, `adapters/qdrant_index.py:183-305`,
  `apps/api/src/dw_api/bootstrap/knowledge.py:20-55`,
  `apps/worker/src/dw_worker/composition.py:100-150`, `agent_factory.py:108-180`,
  `system_prompt.py`, `langchain_usage.py:50-100`, `langgraph_runner.py:210-220`,
  `model/prompts.py:50-100`, `contracts.py:50-80`.
- `CLAUDE.md` "Per-tenant artifacts", "Qdrant retrieval always receives trusted
  tenant/workspace/ACL filters"; `failure-modes.md` #2, #3.

## Comments

### 2026-10-06 — làm xong trên `feat/platform-hardening`

**Đã làm**

1. `document_ids` đi xuống `VectorIndexPort.search(..., document_ids=)`; Qdrant thêm
   `FieldCondition(source_document_id, MatchAny)` vào cùng `must` với điều kiện tin cậy
   (không vào `extra_filters`, không đụng `should` tenant/global); bộ nhớ in-memory làm
   y hệt; dòng lọc sau trong `gateway.py` đã gỡ. Thêm `source_document_id` vào payload
   index (delete/tombstone theo tài liệu cũng lọc theo nó).
2. Một builder: `dw_knowledge/adapters/embedding_factory.py` `build_embeddings(provider,
route, base_url, api_key, profile_id)`; route là Protocol `EmbeddingRoute` mà
   `ModelRoute` thỏa, nên `dw_knowledge` không phụ thuộc `dw_agent_runtime`. API và
   worker gọi nó; thân cũ và docstring chết của worker đã xóa. Để giữ `ConfigError` mà
   không thêm cạnh phụ thuộc, `ConfigError` chuyển sang `dw_kernel.errors`;
   `dw_agent_runtime.registry` re-export (mọi import cũ vẫn chạy, cùng một class).
   Lỗi "thiếu route/key" của worker trước là `InfrastructureError`, nay là `ConfigError`
   như API. Worker giờ resolve profile cả ở chế độ `hash` (profile sai làm hỏng khởi động
   sớm hơn, chặt hơn chứ không lỏng hơn).
3. Prompt agent: theo phương án đề xuất của Câu hỏi 1. `WorkerDefinition` có
   `agent_prompt_id`/`agent_prompt_version` (cả hai hoặc không cái nào; None = graph
   thường). `AgentSpec` bỏ `render_prompt`, thêm `prompt_id`, `prompt_version`,
   `prompts`, `prompt_variables`. `WorkerSystemPrompt` kiểm `PromptRegistry.has` lúc build
   (`ConfigError`), render với `tenant_id=RunContext.tenant_id` mỗi lần gọi (system rồi
   template), và từ chối (`TenantContextMissingError`) khi request không có `RunContext`.
   `track` bắt buộc `prompt_id`/`prompt_version`; hai hằng `AGENT_LOOP_PROMPT*` đã xóa.
   Runner ghi chi phí agent loop theo pin của worker; worker không pin (graph thường) ghi
   `graph:<worker_id>@<graph_version>`, không còn `0.0.0`. `release_manifest.py` đưa pin
   vào mục `workers`; prompt nằm dưới `configs/prompts` nên đã có trong `prompt_bundles`;
   `test_release_manifest` đòi mọi pin trỏ tới một bundle. Thay đổi gọn: manifest ref
   không đổi (repo này không có worker YAML).

**Kiểm tra (đỏ trước: các test mới đỏ trước khi sửa: unit 0/5, Qdrant `assert 0 == 5`,
`ModuleNotFoundError` cho factory, `AgentSpec` không nhận trường mới)**

- `make ci`: xanh (`ruff` sạch, `mypy` 374 file không lỗi, unit 960 passed / 3 skipped,
  import-linter 5 kept 0 broken, verify_architecture 11 package, contract 2 passed,
  eval-smoke `platform_smoke@1.1.0` 4/4, release manifest khớp).
- `uv run pytest -m integration packages/python/dw_knowledge
packages/python/dw_agent_runtime/tests/integration apps/worker/tests apps/api/tests`:
  109 passed (infra của repo này, `make infra-down` sau đó).
- `rg "AGENT_LOOP_PROMPT_VERSION|render_prompt" packages apps`: không còn kết quả.

**Chứng minh đỏ (phá, chạy, đỏ, khôi phục; khôi phục so byte với bản trước)**

| Chốt                       | Đột biến                                                             | Test đỏ                                                                                                                                   |
| -------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Lọc trước top-k            | gateway quay về lọc sau                                              | `test_a_requested_document_below_the_global_top_k_still_fills_top_k` (Qdrant thật)                                                        |
| Không thay bộ lọc tin cậy  | có `document_ids` thì `must` chỉ còn điều kiện tài liệu, bỏ `should` | `test_naming_another_tenants_document_returns_nothing`                                                                                    |
| Một builder                | thêm lại nhánh `HashEmbeddingAdapter()` trong worker                 | `test_only_the_factory_constructs_an_embedding_adapter`                                                                                   |
| Prompt có version (ledger) | runner ghi `"agent_loop","0.0.0"`; runner gọi `track` không prompt   | `test_an_agent_loop_is_billed_under_the_prompt_its_worker_pins`, `test_a_plain_graph_is_billed_under_its_graph_version_not_a_made_up_one` |
| Prompt thiếu → lỗi build   | bỏ kiểm `prompts.has`                                                | `test_a_prompt_missing_from_the_registry_fails_the_build`                                                                                 |
| Tenant từ run              | render với `tenant_id=None`                                          | `test_a_tenants_own_prompt_reaches_its_runs_and_no_one_elses`                                                                             |
| Không run thì không prompt | thiếu `RunContext` rơi về bản platform                               | `test_a_run_without_a_run_context_is_not_given_a_prompt`                                                                                  |
| Pin đủ hai nửa             | bỏ validator                                                         | `test_a_prompt_pin_is_both_halves_or_neither` (2 case)                                                                                    |
| Manifest                   | tạm thêm worker YAML pin prompt không có bundle                      | `test_manifest_contains_every_required_section`                                                                                           |

**Còn lại, ghi rõ (không chặn ticket)**

- `AgentSpec.prompt_id/version` và `WorkerDefinition.agent_prompt_*` là hai chỗ host phải
  truyền khớp nhau (docstring nói lấy từ định nghĩa). Chưa có gì bắt lệch vì
  `build_agent` không thấy `WorkerDefinition`; khi context đầu tiên gọi `build_agent`,
  cân nhắc để `AgentSpec` nhận chính `WorkerDefinition`.
- Pin prompt agent chưa nằm trên dòng `platform.worker_runs` (cần migration); hiện
  truy được qua ledger (`run_id`) và manifest theo `worker_version`.
- `SubAgentSpec.system_prompt` vẫn là chuỗi tự do (ngoài phạm vi ticket).
- Mặc định khi chạy process trần: API `model_profile="balanced"`, worker `"gateway"`.
  Compose đặt cả hai từ `DW_API_MODEL_PROFILE` nên không lệch khi deploy; chưa sửa.
- Test ledger đi qua `runner._metered` + `LangchainUsageMeter` thật, không qua
  `runner.start` đầy đủ.
