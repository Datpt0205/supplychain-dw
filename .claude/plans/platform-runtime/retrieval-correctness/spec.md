# Tìm theo tài liệu lọc trước top-k; một chủ cho model embedding; prompt agent loop có version

Status: resolved
Area: platform-runtime · Nhánh: `feat/platform-hardening` · Viết: 6/10/2026

Lát nền tảng, trung tính với sản phẩm. Nguồn: audit harness 6/10/2026 (khu `retrieval`,
`memory-store`, `file-memory-and-instructions`), kiểm lại trên `main` (`4cb45dc`) của
repo này.

## Hiện trạng (đã kiểm trong code)

- **`document_ids` áp sau top-k.** `KnowledgeGateway.search`
  (`packages/python/dw_knowledge/src/dw_knowledge/gateway.py:575-582`) lấy `fetch_k` hit
  từ `vector_index.search(vector, trusted_filter, fetch_k, query.filters)` rồi mới lọc
  `[h for h in hits if h.document_id in query.document_ids]`. Hỏi về một tài liệu mà các
  chunk của nó không nằm trong top-k toàn cục thì nhận ít kết quả hơn, hoặc không gì.
  `QdrantVectorIndex.search` (`adapters/qdrant_index.py:240-305`) đã dựng `must` từ
  `trusted_filter` và `extra_filters`; payload có `source_document_id`
  (`qdrant_index.py:159-160, 183-190`).
- **Hai builder embedding.** `apps/api/src/dw_api/bootstrap/knowledge.py:20-55` và
  `apps/worker/src/dw_worker/composition.py:100-130` cùng trả lời "model nào embed văn bản
  của deployment này". Docstring của bản worker nói chính điều đó là drift, rồi bản thứ
  hai vẫn tồn tại; ngay sau là một chuỗi docstring thứ hai chết (`composition.py:104-109`).
  Hai bản đọc cùng route của profile, và `QdrantMemoryRanker.ensure_ready` từ chối lệch
  chiều; rủi ro còn lại là đổi model cùng chiều ở một bản.
- **Prompt agent loop không phải artifact có version.** `AgentSpec.render_prompt` là một
  callable tự do (`adapters/agent_factory.py:129`), `WorkerSystemPrompt` nhận nó
  (`adapters/system_prompt.py:37-53`). Runner ghi chi phí agent loop dưới
  `AGENT_LOOP_PROMPT = "agent_loop"`, `AGENT_LOOP_PROMPT_VERSION = "0.0.0"`
  (`adapters/langchain_usage.py:55-56`, `adapters/langgraph_runner.py:217-219`). Không qua
  `PromptRegistry` (có `TenantOverlay`, `model/prompts.py:50-100`), không vào release
  manifest. Trái `CLAUDE.md` "Every … prompt … is versioned". Hôm nay
  `AgentSpec(` không có caller ngoài test và `configs/workers/` chỉ có README, nên thay
  đổi gọn: không caller production nào phải sửa.

Đã sửa, bỏ khỏi lát: reranker hỏng làm hỏng cả lượt tìm (audit, khu `retrieval`) đã được
`4cb45dc` sửa (giữ thứ tự vector, `rerank_skipped`, metric).

## Mục tiêu

1. `document_ids` thành một điều kiện `MatchAny` trên `source_document_id`, do knowledge
   gateway thêm cạnh bộ lọc tin cậy tenant/workspace/ACL, không bao giờ thay nó; áp
   trước top-k.
2. Một builder embedding dùng chung cho api và worker; docstring worker đúng.
3. Prompt của agent loop là `(prompt_id, prompt_version)` render qua `PromptRegistry` với
   tenant từ `RunContext`; chi phí ghi đúng version đó; `"0.0.0"` mất.

## Quy tắc và quyết định

- Bộ lọc tài liệu là giao với bộ lọc tin cậy: chỉ thu hẹp. Nó đi vào `must` cùng các điều
  kiện tin cậy, không vào `extra_filters` (đường đó cho bộ lọc nghiệp vụ người gọi chọn),
  và không làm `trusted_filter` tùy chọn ở bất kỳ đâu.
- Builder dùng chung nằm ở chỗ cả hai app import được mà không thêm cạnh phụ thuộc mới;
  `lint-imports` và `scripts/verify_architecture.py` xanh. Nó nhận route đã resolve và
  thông tin nhà cung cấp, không nhận `ApiSettings`/`WorkerSettings`.
- Prompt agent loop: tenant lấy từ run (`RunContext.tenant_id`), không từ request; thiếu
  prompt trong registry → lỗi khi build, không rơi về callable.

## Trong phạm vi

- `dw_knowledge/gateway.py`, `dw_knowledge/ports.py` (chữ ký `VectorIndexPort.search` nếu
  cần), `adapters/qdrant_index.py`, fake index trong test.
- `apps/api/src/dw_api/bootstrap/knowledge.py`, `apps/worker/src/dw_worker/composition.py`,
  chỗ dùng chung mới.
- `dw_agent_runtime/adapters/agent_factory.py`, `system_prompt.py`, `langchain_usage.py`,
  `langgraph_runner.py`, `contracts.py` (`WorkerDefinition` khai prompt agent nếu runner
  cần biết version để ghi chi phí), `scripts/release_manifest.py`.

## Ngoài phạm vi

- Nhánh lexical/BM25 và fusion: chờ eval retrieval có dữ liệu thật cho thấy trượt mã định
  danh.
- Bộ golden retrieval và eval smoke: thuộc use case knowledge đầu tiên của một sản phẩm.
- Cấu hình rerank ở các repo sản phẩm: việc đồng bộ sản phẩm, không phải ticket nền tảng.

## Quy tắc và kiểm soát

| Chốt                                      | Test đỏ khi gỡ                                                                                                                                   |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tài liệu được hỏi lọc trước top-k         | Quay về lọc sau → test "tài liệu hỏi xếp dưới top_k toàn cục vẫn trả đủ top_k" đỏ (Qdrant thật)                                                  |
| Bộ lọc tài liệu không thay bộ lọc tin cậy | Thay `must` tin cậy bằng điều kiện tài liệu → test hỏi `document_ids` của tenant B từ tenant A đỏ (`test_qdrant_tenant_filter.py` thêm biến thể) |
| Một builder embedding                     | Thêm lại builder riêng ở một app → test (hoặc kiểm kiến trúc) "chỉ một định nghĩa dựng embedding" đỏ                                             |
| Prompt agent có version                   | Gọi `track` không prompt → test ledger ghi `prompt_version` của registry đỏ; prompt thiếu trong registry → lỗi build                             |
| Prompt theo tenant từ run                 | Render với tenant từ request/khác run → test overlay (tenant A có bản riêng, B rơi về platform) đỏ                                               |

## Tiêu chí xong của slice

- Ticket 01 `Status: resolved`, chứng minh đỏ dưới `## Comments`.
- `make ci` xanh; integration Qdrant của `dw_knowledge` xanh.
- Dòng của lát trong `.claude/plans/platform-runtime.md`.

## Phụ thuộc

- Không chờ lát nào.

## Câu hỏi còn mở

1. Prompt agent pin ở `WorkerDefinition` (một trường mới, worker YAML khai) hay chỉ ở
   `AgentSpec`? Đề xuất: `WorkerDefinition`, vì runner ghi chi phí và release manifest đọc
   từ đó; `AgentSpec` nhận nó từ định nghĩa, không khai lần hai. Nếu đổi
   `WorkerDefinition` kéo theo thay đổi lớn ở manifest, dừng phần 3 và ghi chính xác chỗ
   vướng vào ticket.

## Danh sách ticket

| #   | Ticket                                                                                                                                        | Status   | Blocked by |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------- | ---------- |
| 01  | [Lọc tài liệu trong Qdrant, một builder embedding, prompt agent loop có version](issues/01-document-filter-embedding-builder-agent-prompt.md) | resolved | —          |
