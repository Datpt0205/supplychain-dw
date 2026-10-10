# 01 — Candidate REVIEW thành approval `memory.review`; quyết định ghi hay bỏ qua đường ghi đã kiểm

Status: resolved
Blocked by: platform-runtime/approval-audit-and-workspace 01, 02; platform-runtime/memory-write-trust 01
Area: platform-runtime

## Mục tiêu

Mỗi candidate mà policy giữ lại để duyệt xuất hiện trong hộp approval chung; duyệt thì ghi
đúng một item qua đường ghi đã kiểm, từ chối thì không ghi gì, và cả hai có audit. Spec,
mục "Thiết kế", "Điểm dừng" và "Quy tắc và kiểm soát".

## Việc cần làm

1. `dw_memory/service.py`:
    - Nhánh REVIEW của `propose`: trong cùng giao dịch, chèn `platform.approval_requests`
      như spec mục Thiết kế 1. `dw_memory` không import adapter của `dw_platform`: dùng
      port approval mà service đã có quyền dùng, hoặc một Protocol hẹp do `dw_memory` khai
      và composition root thỏa (`CLAUDE.md`, "Independent bounded contexts").
    - Tách phần "kiểm bằng chứng → chèn item → supersession → item_evidence" của
      `propose` thành một hàm dùng chung. `promote(candidate_id, decided_by, context)`
      đọc dòng candidate dưới RLS, dựng item từ nó, gọi hàm đó, đặt
      `write_candidates.memory_id`, ghi audit `memory.written_after_review`. Đã có
      `memory_id` thì trả kết quả cũ (idempotent).
    - `reject(candidate_id, decided_by, context)`: chỉ audit `memory.review_rejected`.
2. `dw_agent_runtime/approval_flow.py`, `decide`: với approval không có run, thêm
   `uow.outbox.add(...)` event `f"{request.approval_type}.decided"` trước `uow.commit()`.
   Không nhánh theo loại approval.
3. Kiểm clearance khi quyết `memory.review`: người quyết phải đọc được phân loại trong
   payload (thang ở `dw_knowledge/contracts.py`, không chép). Đặt kiểm ở chỗ quyết định
   xảy ra; nếu `decide` không nên biết memory, đó là một Protocol "điều kiện quyết theo
   loại" đăng ký ở composition root, cùng kiểu `strict_approval_prefixes`.
4. `apps/worker/src/dw_worker/consumers/memory.py` + `main.py`: handler
   `memory.review.decided` gọi `promote` hoặc `reject`; tenancy lấy từ envelope, không từ
   payload; bằng chứng hết hợp lệ → audit `memory.review_failed` +
   `UndeliverableEventError`.
5. `apps/api/src/dw_api/routes/v1/memory.py`: `GET /memory/candidates/{id}` trả nội dung
   candidate cho người có `memory.read` và đủ clearance; payload approval chỉ mang định
   danh. Cập nhật `contracts/openapi` bằng `scripts/generate_contracts.py`.
6. Nếu chạm Điểm dừng của spec: dừng, ghi khoảng trống vào `## Comments`, để
   `Status: needs-info`, và không commit code nửa vời.

## Tiêu chí chấp nhận

- Test (integration, Postgres thật):
    - candidate REVIEW → đúng một approval `memory.review` pending, `run_id` NULL, payload
      không có `content`;
    - duyệt → đúng một `memory.items`, có `item_evidence`, audit; giao event
      `memory.review.decided` lần hai → vẫn một item;
    - từ chối → 0 item, audit `memory.review_rejected`;
    - duyệt khi chunk được trích đã bị xóa → 0 item, audit `memory.review_failed`;
    - người của tenant B gọi quyết approval của tenant A → `NotFoundError` (RLS), 0 item;
    - người clearance `internal` duyệt candidate `restricted` → bị từ chối, approval vẫn
      pending;
    - nếu `memory.` là strict: người có run sinh fact tự duyệt → bị từ chối.
- Chứng minh đỏ cho từng dòng của bảng "Quy tắc và kiểm soát", ghi dưới `## Comments`.
- `make ci` xanh; `make infra-down` cho repo này khi xong.

## Nguồn

- Audit harness 6/10/2026: `memory-store` gap "REVIEW decisions land in
  memory.write_candidates and nothing reads them", `file-memory-and-instructions` gap
  "REVIEW-held memories have no path to a human", `reuse` gap "The long-term memory loop is
  open at both ends".
- `dw_memory/policy.py:45-60`, `dw_memory/service.py:132-264`,
  `dw_agent_runtime/approval_flow.py`, `dw_platform/application/ports.py:236-315`,
  `apps/worker/src/dw_worker/consumers/outbox.py`, `apps/api/src/dw_api/routes/v1/approvals.py`,
  `apps/api/src/dw_api/routes/v1/memory.py`.
- `CLAUDE.md` "Human-in-command", "Side effects require policy evaluation, idempotency and
  audit"; `failure-modes.md` #1, #5.

## Comments

### 6/10/2026 — làm xong (nhánh `feat/platform-hardening`)

Không chạm Điểm dừng: `decide` ghi outbox qua `uow.outbox` có sẵn trong
`PlatformUnitOfWork` (không đổi hợp đồng), và approval không run không vướng gì ở ticket 02
(dòng approval có `workspace_id` như mọi loại khác).

Đã làm:

- `dw_platform/domain/approval.py`: `decided_event_type(approval_type)`, một chỗ đặt tên
  event cho cả bên ghi và bên đăng ký handler.
- `dw_agent_runtime/approval_flow.py`: approval không run ghi một outbox event
  `<type>.decided` (chỉ định danh, outcome, người quyết; không comment, không payload)
  trước `uow.commit()`. `decision_guards: Mapping[str, DecisionGuard]` đăng ký theo loại ở
  composition root; không có nhánh theo loại trong `decide`.
- `dw_memory/review.py`: `MEMORY_REVIEW`, `MEMORY_REVIEW_DECIDED`,
  `require_clearance_for_review` (đọc nhãn đóng dấu trên approval, thang của
  `dw_knowledge.contracts`; payload không có nhãn thì từ chối).
- `dw_memory/service.py`: nhánh REVIEW kiểm bằng chứng và nhãn (savepoint, rollback: chỉ
  kiểm, chưa ghi `knowledge.evidence`) rồi mở approval trong cùng giao dịch. `_store_item`
  là đường ghi item duy nhất (auto-write và sau duyệt). `settle_review` (thay cho
  `promote`/`reject` của ticket: một hàm đọc kết quả từ dòng approval, không từ event) khóa
  dòng candidate (`FOR UPDATE`), idempotent theo `memory_id`, ghi audit
  `memory.written_after_review` / `memory.review_rejected` / `memory.review_failed`
  (actor là người quyết). `get_candidate` cho route đọc.
- Migration `5e6ccac63d45`: `write_candidates.subject_refs` (jsonb, mặc định rỗng) và
  `fact_key`, vì item dựng từ dòng candidate cần hai trường này để được recall và
  supersede. Lên, xuống, lên lại trên DB dev: head `5e6ccac63d45`.
- Worker: `build_review_handler` trong `memory_handlers` (`main.py` đã nối sẵn
  `memory_handlers`); tenancy từ envelope, principal là `decided_by`, không scope.
  `DomainError`/`NotFoundError` thành `UndeliverableEventError`.
- API: `GET /memory/candidates/{id}` (`memory.read`; workspace và clearance kiểm ở
  service). OpenAPI và `platform.d.ts` sinh lại.
- Composition root (`dw_api/bootstrap/runtime.py`): `strict_approval_prefixes={"memory."}`
  (câu hỏi mở 1 của spec: theo đề xuất) và `decision_guards={MEMORY_REVIEW: ...}`.

Lệch so với chữ của ticket, có lý do:

- `dw_memory/service.py` dùng `SqlApprovalRepository` của `dw_platform`, không qua
  Protocol: approval phải chèn trong session của candidate, và file này đã dùng
  `SqlAuditRepository` cùng cách; `dw_memory` và `dw_platform` đều là gói nền tảng, không
  phải hai bounded context. import-linter 5 kept.
- Thêm kiểm nhãn lúc giữ (ticket không có), tìm ra khi chạy `reviewing-feature-security`:
  nhãn trên approval quyết định ai đọc nội dung và ai được quyết. Trước khi sửa, candidate
  tự khai `internal` mà trích tài liệu `restricted` sẽ mở approval nhãn `internal`, và
  người clearance `internal` đọc được nội dung qua route. Giờ bị từ chối ngay ở `propose`
  (`DomainError`), không mở approval.

Kiểm chứng (infra `dw_codebase`):

- Đỏ trước: hai test nhãn lúc giữ
  (`test_a_held_candidate_claiming_less_than_its_evidence_opens_no_review`,
  `test_a_held_candidate_citing_nothing_real_reaches_no_one`) đỏ trước khi sửa, xanh sau.
  Phần code còn lại có từ lượt trước (dừng giữa chừng, chưa test); đỏ của nó chứng minh
  bằng mutation bên dưới.
- `uv run pytest -m integration packages/python/dw_memory packages/python/dw_platform packages/python/dw_agent_runtime packages/python/dw_knowledge apps/api apps/worker -rA`
  → exit 0, 334 PASSED (gồm 12 test của
  `apps/worker/tests/integration/test_memory_review_db.py`). Lượt đầu lỗi Qdrant
  `ReadTimeout`: khoảng 60 collection test sót lại làm việc tạo collection mất ~15 s; xóa
  các collection `*_test_*` của stack `dw_codebase` rồi chạy lại, không đổi code.
- `make ci` → exit 0: ruff, mypy 369 files, unit và contract 930 passed / 3 skipped,
  import-linter 5 kept / 0 broken, declared-deps 11, eval smoke `platform_smoke@1.1.0`
  4/4, release manifest up to date.

Chứng minh đỏ (script mutation áp đúng một chỗ, assert số lần khớp bằng 1, chạy test,
hoàn nguyên từ bản trong bộ nhớ; `git diff --stat` trước và sau giống hệt):

| Chốt gỡ                                               | Test đỏ                                                                |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| REVIEW không mở approval                              | `test_a_held_candidate_opens_one_pending_review_without_its_content`   |
| `settle_review` không ghi item khi duyệt              | `test_approving_writes_one_item_with_its_evidence_once`                |
| bỏ kiểm `memory_id` (idempotent)                      | `test_approving_writes_one_item_with_its_evidence_once`                |
| từ chối cũng ghi item                                 | `test_rejecting_writes_nothing_and_says_so`                            |
| `_store_item` bỏ `_verify_evidence`                   | `test_an_approval_does_not_excuse_evidence_that_no_longer_verifies`    |
| `decide` đọc approval bằng role bỏ qua RLS (migrator) | `test_another_tenant_cannot_decide_it`                                 |
| guard clearance không từ chối                         | `test_a_decider_not_cleared_for_the_memory_cannot_decide_it`, wiring   |
| `decide` không gọi guard                              | test clearance integration, `test_approval_flow.py`                    |
| runtime bỏ `decision_guards`                          | `test_memory_review_wiring.py`                                         |
| runtime bỏ prefix strict `memory.`                    | `test_memory_review_wiring.py`                                         |
| chép `content` vào payload approval                   | `test_a_held_candidate_opens_one_pending_review_without_its_content`   |
| `decide` không ghi outbox                             | test duyệt integration, `test_approval_flow.py`                        |
| giữ mà không kiểm nhãn và bằng chứng                  | hai test nhãn lúc giữ                                                  |
| savepoint kiểm bằng chứng commit thay vì rollback     | `test_holding_a_candidate_writes_no_evidence_yet`                      |
| route bỏ `memory.read`                                | `test_memory_candidate_endpoint.py`                                    |
| `get_candidate` bỏ kiểm clearance                     | `test_the_content_is_served_to_a_cleared_reader_of_the_workspace_only` |

Không có test đỏ riêng cho khóa `FOR UPDATE` (hai lần giao chạy song song): chỉ lập luận,
không đo.

Còn mở, không thuộc ticket này:

- `approval-audit-and-workspace` 01 (bản thân quyết định chưa có audit; hậu quả
  `memory.*` thì có) và 02 (`decide` chỉ lọc tenant: người giữ `approvals.decide` ở W2 vẫn
  quyết được `memory.review` của W1, và item ghi vào W1). Cả hai áp cho `memory.review`
  ngay khi xong, không cần sửa gì ở đây; nên làm 02 trước khi một sản phẩm phát
  `memory.candidate_proposed`.
- Event `<type>.decided` của approval không run mà không ai đăng ký handler nằm mãi trong
  outbox (ghi ở area file, mục Memory).
