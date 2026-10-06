# 02 — Approval, run và audit đọc theo workspace; route nào cũng kiểm scope

Status: resolved
Blocked by: —
Area: platform-runtime

## Mục tiêu

Một thành viên chỉ thấy approval, run và audit của workspace mình, và mỗi route đọc kiểm
đúng scope của nó. Hôm nay RLS của ba bảng chỉ lọc tenant, nên thành viên của một workspace
đọc được payload approval, kết quả run và chi tiết audit của mọi workspace khác trong tenant;
`GET /runs/{id}` không kiểm scope nào; route audit kiểm `approvals.read` (vai `member` có)
thay vì `audit.events` (spec, Hiện trạng). Một context đặt dữ liệu nghiệp vụ vào payload,
kết quả run hay chi tiết audit sẽ lộ nó cho cả tenant.

## Việc cần làm

- Approval:
    - `list_pending` lọc `workspace_id` của người gọi (lấy từ `AccessContext`, không từ
      client); fingerprint của cursor mang workspace như đã mang tenant;
    - `get` theo id (dùng ở `GET /approvals/{id}` và ở `decide`) lọc cùng workspace; approval
      của workspace khác là 404, không phải 403;
    - run chạy tiếp lấy `workspace_id` của run (cột `workspace_id` của `worker_runs`, ghi ở
      `run_store.py:354`), không của người quyết (`approval_flow.py:151`). `RunRecord`
      (`run_store.py:70-103`) chưa mang cột này: thêm trường và đọc nó ở `get`. Sửa chú thích
      sai ở `approval_flow.py:118-119`.
- `GET /runs/{run_id}`: `authorization.require(action="runs.read", ...)` như
  `get_timeline`; `run_store.get` lọc workspace của người gọi, run của workspace khác là 404.
- `GET /audit/events`: kiểm `audit.events`, không kiểm `approvals.read`; chỉ trả dòng của
  workspace người gọi.
- Không đổi policy RLS ở ticket này (spec, Ngoài phạm vi): lọc ở repository, có test âm.

## Tiêu chí chấp nhận

Integration (`apps/api`, DB thật; một tenant, hai workspace W1, W2; A là thành viên W1, B là
thành viên W2): _(Sửa 5/10/2026, quyết định tạm 5: `apps/api` không có suite integration;
test DB thật nằm ở `dw_platform/tests/integration/test_workspace_reads.py` và
`dw_agent_runtime/tests/integration/test_approval_workspace.py`; hành vi 403/404 của route ở
test unit `apps/api/tests/unit/test_run_and_audit_reads.py` và `test_approvals_endpoint.py`.)_

- [x] B gọi `GET /approvals`: không có approval nào của W1. `GET /approvals/{id}` với id của
      W1: 404.
- [x] B giữ `approvals.decide` ở W2 quyết approval của W1: 404; approval vẫn `pending`,
      không có dòng `approval_decisions`, run của W1 không chạy tiếp.
- [x] A quyết approval của W1: run chạy tiếp với `workspace_id = W1` (khẳng định trên
      `RunContext` mà runner giả nhận).
- [x] B gọi `GET /runs/{id}` với run của W1: 404. Người không có `runs.read`: 403.
- [x] B gọi `GET /audit/events`: không có dòng nào của W1. Người chỉ có `approvals.read`
      (vai `member`): 403; người có `audit.events` (vai `director`): 200.
- [x] Mutation, ghi vào Comments: bỏ điều kiện workspace ở `list_pending` thì ca đầu đỏ; bỏ
      `require` ở `GET /runs/{id}` thì ca 403 đỏ.
- [x] Trang `/approvals` và `/audit` của web vẫn chạy với người có scope. _(Xem trong
      trình duyệt 6/10/2026, Comments "vòng review 1", mục 3 và 7.)_
- [x] `make ci` xanh; hợp đồng OpenAPI và client sinh lại nếu đổi.

## Nguồn

- `apps/api/src/dw_api/routes/v1/approvals.py:44-105`, `runs.py:41-58`, `audit.py:39-72`.
- `packages/python/dw_platform/src/dw_platform/adapters/persistence/repositories.py:98-145`
  (`get`, `list_pending`); `packages/python/dw_agent_runtime/src/dw_agent_runtime/approval_flow.py:118-119, 151`;
  `packages/python/dw_agent_runtime/src/dw_agent_runtime/adapters/run_store.py:70-103, 354, 412-418`.
- `db/migrations/sql/0001_platform_baseline.sql:839, 842, 875` (policy chỉ lọc tenant);
  `db/migrations/sql/0001_platform_reference.sql:68, 74` (`audit.events`).
- `CLAUDE.md` "Tenancy and authorization" (test âm chéo tenant và chéo workspace là bắt
  buộc; ẩn nút không phải phân quyền).

## Comments

### 2026-10-05: ticket đã làm (implementer); quyết định tạm của lead, chờ Đạt duyệt ở QO-2

Nhánh `feat/elmich-a-d-s1`, trên `6a7c2fa`; chưa commit.

**Quyết định tạm (bắt buộc cho lát này), mỗi cái đã làm:**

1. **Làm như ticket, lọc ở repository, không đổi RLS.** Các lệnh đọc nhận `workspace_id`
   là tham số keyword BẮT BUỘC: `ApprovalRepositoryPort.get(id, *, workspace_id)`,
   `list_pending(request, *, workspace_id)`, `AuditRepositoryPort.list_page(request, *,
workspace_id)`, `list_for_run(run_id, *, workspace_id, limit)`. Không có giá trị mặc định,
   nên một chỗ gọi quên nó thì mypy từ chối (mypy đã chỉ ra đúng 14 chỗ gọi trong test khi
   đổi). Người gọi truyền `context.workspace_id` của `AccessContext` server, không từ client.
   `RunRecord` thêm `workspace_id` (đọc ở `get`); `SqlWorkerRunStore.get` lọc
   `workspace_id == run_context.workspace_id`. `decide` đọc approval trong workspace người gọi
   và resume với `workspace_id=record.workspace_id`. `GET /runs/{id}` gọi
   `authorization.require(action="runs.read")` trước khi đọc; run của workspace khác là 404
   giống run không tồn tại. `GET /audit/events` kiểm `audit.events`, chỉ trả dòng của
   workspace người gọi. Fingerprint của cursor `approvals.pending` và `audit.events` mang
   thêm `workspace`. Chú thích sai ở `approval_flow.py` đã sửa ("tenant-scoped by RLS and
   workspace-scoped by the read above").
   _Cách đã cân nhắc và bỏ:_ (a) repository lấy workspace từ UoW (đúng một chủ). Lý do
   bỏ ghi lúc đầu ("khoảng 15 chỗ dựng `SqlAuditRepository(session)` phải thêm một tham số
   vô nghĩa") **sai**: các chỗ đó chỉ `append`, nên một tham số tùy chọn ở constructor mà
   chỉ UoW truyền thì không chạm chúng (review vòng 1, phát hiện 4). Đánh đổi thật, ghi ở
   "Vòng review 1" bên dưới; (b) lọc theo GUC `app.workspace_id` trong SQL: không khớp
   cách các repository khác của repo viết (`.workspace_id == context.workspace_id`) và khó
   thấy khi đọc.
2. **Người đọc thứ hai và mọi người đọc khác.** Đã lọc theo workspace người gọi:
   `SqlPendingApprovalQuery.list_pending_by_type_prefix` (brief của supply-chain chỉ thấy
   approval mà hộp `/approvals` cũng cho thấy; docstring `PendingApprovalsPort` ghi điều
   đó; hai fake của port cũng lọc workspace); timeline `GET /runs/{id}/timeline`
   (`list_for_run`); `thread_belongs_to(tenant, workspace, thread)` của
   `POST /runs/threads/{id}/cancel` (B không dừng được run của W1). Đã xem và **không**
   lọc, có lý do: `started_since` (quota theo gói của cả tenant, cố ý), `waiting_approval_for_thread`
   và `_reap_stale_thread` (chỉ trên đường claim của `create`, với `thread_id` lấy từ
   `RunContext` của chính run; hôm nay không route nào nhận `thread_id` từ client để chạy
   run), `active_since` (không có caller trong `src`), offboarding (SQL riêng, cố ý đọc mọi
   workspace qua `app.workspace_scope='tenant'`). Mọi chỗ khác chạm `audit_events` chỉ ghi
   (`append`, `zalo_link_repo`, `offboarding._record_purge_audit`). `apps/worker` không đọc
   approval, run hay audit (đã grep).
3. **Offboarding:** không dùng các repository này (SQL theo catalog, `WHERE tenant_id`), nên
   không đổi; `test_offboarding.py` nằm trong 206 test integration của `dw_platform`, xanh.
4. **Đường worker/hệ thống đọc chéo workspace:** không có. Người gọi `run_store.get`:
   route (context người gọi), `decide` (người gọi, đã cùng workspace với approval),
   `LangGraphWorkflowRunner.resume` (context mà `decide` dựng, nay mang workspace của run),
   `_settle_stream` (context của chính run). Người gọi `approvals.get`/`list_pending` và
   audit: chỉ route và `decide`. `GetDailyBrief` chỉ gọi từ route. Integration
   `dw_supply_chain` (S1, `test_advance_case_approval.py`) xanh: helper đọc run của test nay
   truyền workspace của case thay vì một UUID ngẫu nhiên.
5. **Test âm ở nơi có hiệu lực, DB thật:** `dw_platform/tests/integration/test_workspace_reads.py`
   (6: inbox, get theo id, count của `SqlPendingApprovalQuery`, audit page, timeline, và
   catalog `member` không có `audit.events` còn `director` có) và
   `dw_agent_runtime/tests/integration/test_approval_workspace.py` (4, runner thật bọc một
   `RecordingRunner`). Route: `apps/api/tests/unit/test_run_and_audit_reads.py` (mới, 8),
   `test_approvals_endpoint.py` (+4), `test_cancel_endpoint.py` (fake hỏi cả workspace).
   Unit service: `test_approval_flow.py` (+2: approval của workspace khác; run khác workspace
   với approval thì `run not found`, không ghi gì).
6. Đã thêm một dòng vào Comments của `supply-chain/approval-decider-scope/issues/01` (tên
   test) và một đoạn "Sửa đổi 2026-10-05" thứ hai vào ADR 0020.

**Lệch khỏi ticket/quyết định (cần lead xem):**

- **Migration `cbf765d02a12`** (head trước: `7c422b849fe9`; một head). Lead ghi "có lẽ không
  cần migration". Có thêm vì các lệnh đọc nay lọc workspace mà index keyset dẫn đầu
  `tenant_id` rồi đi thẳng tới cột sắp xếp: một workspace không có gì chờ trong một tenant
  mà workspace khác có hàng nghìn dòng sẽ đọc hết chỗ đó để trả một trang rỗng (điều
  `0003_keyset_indexes` viết ra để chặn). `ix_approval_requests_page` dựng lại thành
  `(tenant_id, workspace_id, status, created_at DESC, id DESC)`; thêm `ix_audit_events_page`
  `(tenant_id, workspace_id, occurred_at DESC, id DESC)` trên bảng cha phân vùng.
  `ix_audit_events_tenant_time` giữ nguyên. Đã đo: upgrade, downgrade -1, upgrade trên
  `dw_test` (index cũ về đúng, rồi lại mới); 6 phân vùng có index mới, và 3 phân vùng
  `platform.ensure_time_partitions(5)` tạo SAU migration cũng có (9/9). Không có test đo
  plan; không có tiền lệ EXPLAIN trong repo.
- **Web:** mục "Audit log" của nav (`apps/web/lib/nav/registry.ts`) đổi scope từ
  `approvals.read` sang `audit.events`, nếu không mọi `member` thấy một link tới trang mà API
  nay từ chối (đường đọc và đường ghi cùng nguồn). Vitest mới
  `apps/web/app/__tests__/home-page.test.tsx`. Trang `/audit` (shadcn) **không** sửa: người
  gõ thẳng URL mà thiếu scope thấy lỗi API sẵn có của trang, không phải `Result 403`; chuyển
  trang sang antd và trạng thái 403 là việc của ticket W. _(Vòng review 1: lần đầu chỉ sửa
  registry; link "Audit log" thứ hai, trong menu tài khoản `session-chip.tsx`, vẫn kiểm
  `approvals.read`. Đã sửa, xem dưới.)_
- **Không đổi hợp đồng API:** `make generate-contracts` chỉ đổi CRLF của `openapi.json`
  (`git diff -w` rỗng), đã trả file về; `platform.d.ts` không đổi.

**Đi qua `reviewing-feature-security`:**

- Cô lập (tenant/workspace): áp dụng. Không bảng mới, RLS không đổi. Test âm khẳng định
  kết quả rỗng/None/not found (không chỉ kiểu lỗi): inbox không có approval của W1, `get`
  trả None, `decide` là not found **của approval** (`message` và `details` được khẳng định,
  để `run not found` phía sau không giữ test xanh), audit page và timeline không có dòng
  của W1, `run_store.get` là `run not found`, `thread_belongs_to` False. Cache phía web đã
  gộp workspace vào khóa (`use-cached-resource.ts`); cursor mang workspace (test 422).
- Phân quyền: áp dụng. Kiểm ở route nơi đọc xảy ra, gọi thẳng HTTP không qua trang:
  thiếu `runs.read` thì 403 và store không được hỏi; `approvals.read` không đủ cho audit
  (403, repo không được hỏi), `audit.events` thì 200. Danh tính và workspace từ
  `AccessContext`. Không dấu mới.
- Autonomy/approval: không thêm tầm với nào cho agent. Run chạy tiếp với workspace của
  run, cùng giá trị với workspace người quyết sau lần đọc đã lọc (xem M10).
- Nội dung không tin cậy: không áp dụng; không có đầu vào mới, id là UUID FastAPI parse.
- Audit/provenance: không có ghi mới; chỉ thu hẹp lệnh đọc. Audit của quyết định là
  ticket 01 (còn mở).
- Mặc định: `workspace_id` không có mặc định (thiếu là lỗi type); workspace lệch là not
  found (đóng).
- Không chạm Dockerfile, lockfile, compose; không đường nào phụ thuộc môi trường, nên không
  chạy `reviewing-deployment-security`.

**Lệnh đã chạy (5–6/10/2026, Windows, Git Bash):**

| Lệnh                                                                  | Kết quả                                                                                         |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `make lint`                                                           | xanh (exit 0): ruff check/format, prettier, eslint web chỉ cảnh báo có sẵn                      |
| `make typecheck`                                                      | xanh: mypy 467 file không lỗi; tsc contracts, ui, api-client, web                               |
| `make test-unit`                                                      | xanh: 1803 passed, 3 skipped                                                                    |
| `make test-architecture`                                              | xanh: import-linter 9 kept, 0 broken; declared-dependency 12 gói; invariants ok                 |
| `make test-contract`                                                  | xanh: 5 passed                                                                                  |
| `make generate-contracts`                                             | chạy; chỉ CRLF ở `openapi.json`, đã trả về; không đổi hình dạng API                             |
| `make release-manifest-check`                                         | xanh (không thêm config có phiên bản)                                                           |
| `make eval-smoke`                                                     | xanh: platform 4/4, supply_chain 22/22                                                          |
| `pytest -m integration packages/python/dw_platform/tests` (.env)      | xanh: 206 passed (lần chạy cuối, đã có cả 6 test của `test_workspace_reads.py`)                 |
| `pytest -m integration packages/python/dw_agent_runtime/tests` (.env) | xanh: 61 passed (57 + 4 mới)                                                                    |
| `pytest -m integration packages/python/dw_supply_chain/tests` (.env)  | xanh: 164 passed                                                                                |
| `vitest run app/__tests__/home-page.test.tsx app/approvals/...`       | xanh: 2 file, 7 test                                                                            |
| `make ci`                                                             | xanh (exit 0, ">> local CI gate passed"): unit 1803, contract 5, eval 4/4 và 22/22, manifest OK |

**Mutation** (script: control xanh 16/16 trước; mỗi ca sửa đúng một chỗ, khẳng định file
đã đổi, chạy test canh nó, ghi lại bytes gốc và so sha256; `git diff` sau cùng cho thấy cả
12 guard Python M1–M12 còn đó; M13 (web) chạy riêng bằng vitest, khôi phục cùng cách):

| #   | Bỏ guard                                           | Kết quả                                                                                                                     |
| --- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| M1  | `list_pending` bỏ điều kiện workspace              | **đỏ** 2: `test_the_inbox_lists_only_the_callers_workspace`, `test_another_workspace_neither_sees_nor_decides_the_approval` |
| M2  | `approvals.get` bỏ điều kiện workspace             | **đỏ** 3: `test_another_workspaces_approval_is_not_found_by_id` và hai test `decide` chéo workspace (có run / không run)    |
| M3  | `GET /runs/{id}` bỏ `require(runs.read)`           | **đỏ**: `test_reading_a_run_needs_runs_read`                                                                                |
| M4  | `GET /audit/events` kiểm lại `approvals.read`      | **đỏ**: `test_the_audit_trail_needs_audit_events_not_the_inbox_scope`                                                       |
| M5  | `audit.list_page` bỏ điều kiện workspace           | **đỏ** 2 (platform, runtime)                                                                                                |
| M6  | `audit.list_for_run` bỏ điều kiện workspace        | **đỏ** 2 (platform, runtime)                                                                                                |
| M7  | `SqlPendingApprovalQuery` bỏ điều kiện workspace   | **đỏ**: `test_a_contexts_pending_count_is_the_callers_workspace_only`                                                       |
| M8  | `run_store.get` bỏ điều kiện workspace             | **đỏ**: `test_another_workspace_cannot_read_the_run_its_timeline_or_its_audit`                                              |
| M9  | `thread_belongs_to` bỏ điều kiện workspace         | **đỏ**: cùng test trên                                                                                                      |
| M10 | `decide` resume với `context.workspace_id`         | **vẫn xanh**, đúng dự đoán; xem dưới                                                                                        |
| M11 | fingerprint `approvals.pending` không có workspace | **đỏ**: `test_a_cursor_is_bound_to_the_workspace_it_was_issued_in`                                                          |
| M12 | fingerprint `audit.events` không có workspace      | **đỏ**: `test_an_audit_cursor_is_bound_to_the_workspace_it_was_issued_in`                                                   |
| M13 | web: mục Audit log của nav về `approvals.read`     | **đỏ**: vitest `does not offer the audit log to a member who reads only the inbox`                                          |

M10 không giết được bằng một test trung thực: `decide` đọc approval rồi đọc run trong
workspace người quyết, nên khi tới `resume` thì `record.workspace_id ==
context.workspace_id` luôn đúng. Đọc từ dòng của run là để giá trị có một nguồn (như
roles, scopes, autonomy), không phải một chốt riêng. Lệch giữa hai giá trị, nếu có, bị chặn
trước đó và có test: `test_a_run_in_another_workspace_than_its_approval_is_not_resumed`
(`run not found`, không ghi gì). Tiêu chí "A quyết: run chạy tiếp với `workspace_id = W1`"
được khẳng định trên `RunContext` mà `RecordingRunner` nhận
(`test_the_member_of_the_runs_workspace_decides_and_it_resumes_there`).

**Còn lại / ghi chú:**

- ~~Trang `/audit` chưa kiểm trong trình duyệt~~: đã xem ở vòng review 1, xem dưới.
- DB dev cục bộ (`dw`) đã migrate lên `cbf765d02a12` (6/10, vòng review 1).
- RLS của ba bảng vẫn chỉ lọc tenant; đổi sang dạng workspace là quyết định riêng (spec,
  Ngoài phạm vi). Một role đọc thẳng SQL (vd một service ngôn ngữ khác) không có lớp lọc
  này.
- Audit cho cả tenant (Câu hỏi còn mở 1 của spec) chưa có ai cần; route hiện có không nới.
- Ticket 01 (audit `approval.decided`) vẫn mở; lát này không chạm.

### 2026-10-06: vòng review 1 (8 phát hiện, 6 khác nhau), đã kiểm từng cái trên code

**1. Test chéo tenant bị bộ lọc workspace che (security, minor): đúng, đã sửa.** Các test
chéo tenant cho kẻ tấn công một workspace ngẫu nhiên khác, nên sau lát này chúng xanh nhờ
bộ lọc workspace, kể cả khi ranh giới tenant hỏng. Nay kẻ tấn công mang **đúng workspace
id** của nạn nhân (UUID không gắn với tenant, `approval_requests` không có FK workspace),
nên chỉ RLS tenant còn chặn được:
`test_approval_queries.py::test_another_tenants_approvals_are_not_there`,
`test_approval_decider_scope.py::test_the_stamp_decides_who_may_decide` và
`::test_another_tenant_cannot_decide_a_stamped_approval_without_a_run`. Thêm hai chỗ cùng
dạng mà review không nêu: `test_resume_after_restart.py::test_worker_runs_visible_only_in_own_tenant`
(`run_store.get` nay lọc workspace) và
`dw_supply_chain/tests/integration/test_daily_brief.py::test_the_brief_holds_only_the_callers_tenant`
(số approval chờ của brief). Mutation MT1/MT2 bên dưới: đổi policy trong
`0001_platform_baseline.sql` thành `USING (true)` (giữ `WITH CHECK`; DB test dựng lại từ
migration mỗi phiên) thì các test mới **đỏ**, còn bản cũ (workspace khác) **vẫn xanh**,
đúng điều review nói.

**2 và 6. Link "Audit log" trong menu tài khoản (standards + coverage, major): đúng, đã
sửa.** `apps/web/components/session-chip.tsx` kiểm `hasScope("approvals.read")`. Nay đọc
scope từ mục `/audit` của `NAV_ITEMS` (`lib/nav/registry.ts`), theo đúng nghĩa của registry
(`scope` vắng thì ai cũng thấy; mục bị gỡ thì link biến mất), nên scope có một chủ. Vitest
mới `apps/web/components/__tests__/session-chip.test.tsx` (2 test): viết trước, **đỏ** với
code cũ ("does not offer the audit log to a member who reads only the inbox"), xanh sau
sửa. Đã grep `apps/web` tìm `/audit` và `approvals.read`: còn `e2e/feedback.spec.ts` (chỉ
dùng `/audit` làm trang có nút phản hồi, không phụ thuộc scope) và `app/admin/page.tsx`
(nhãn scope); không cái nào là cửa vào `/audit`.

**3 và 7. Tiêu chí "trang `/approvals` và `/audit` vẫn chạy" được tick khi chưa ai chạy
(minor): đúng, nay đã xem.** API chạy cục bộ (`DW_API_AUTH_MODE=dev`, cổng 8299) trên DB
dev `dw` đã migrate; web `next dev` (cổng 3299, `NEXT_PUBLIC_AUTH_MODE=dev`); Chromium
qua Playwright đăng nhập ở `/dev-login`. Dữ liệu dev không có ai giữ `director` và không
có dòng audit hay approval nào, nên đã tạm cho `dieu.hoang` thêm vai `director` và thêm một
dòng audit, một approval chờ ở mỗi workspace (`verify_pr02.w1`, `verify_pr02.w2`), rồi
**xóa hết và trả vai** sau khi xem (`DELETE 2`, `DELETE 2`, `UPDATE 1`; vai về
`member, sc_finance`). Kết quả:

| Người                      | `/audit`                                                     | Menu tài khoản      | `/approvals`       |
| -------------------------- | ------------------------------------------------------------ | ------------------- | ------------------ |
| director, `dieu.hoang`, W1 | 1 dòng, của W1; không lỗi                                    | có link "Audit log" | 1 approval, của W1 |
| member, `an.nguyen`, W1    | dòng đỏ "permission_denied: action not permitted", 0 sự kiện | không có link       | 1 approval, của W1 |

curl cùng lúc: director `GET /audit/events` 200 (1 dòng), member và `bao.pham` 403.
Giới hạn: hai workspace của DB dev thuộc **hai tenant khác nhau**, nên lần xem này chứng
minh scope và việc mỗi người chỉ thấy dòng của mình, không chứng minh chéo workspace cùng
tenant; phần đó do integration (`test_workspace_reads.py`, `test_approval_workspace.py`).
Người thiếu scope vẫn thấy dòng lỗi API của trang shadcn, chưa phải `Result 403` (ticket W).

**4. Workspace đi hai đường, UoW và tham số đọc (standards, minor): đúng là có hai đường;
giữ thiết kế, chờ QO-2.** Không chỗ gọi nào hôm nay truyền workspace khác với context đã mở
UoW (mọi chỗ trong `src` là `context.workspace_id` của cùng context). Phương án của review
(UoW truyền `context.workspace_id` vào constructor, lệnh đọc từ chối khi chưa gắn) cho một
chủ và **không** chạm các chỗ chỉ `append`, nên lý do bỏ ghi lúc đầu đã sửa ở trên. Đánh
đổi còn lại: tham số keyword bắt buộc là lỗi mypy lúc viết; constructor tùy chọn là lỗi lúc
chạy (đóng) ở một repository dựng tay. Không đổi ở vòng này vì quyết định tạm 1 đã chọn hình
này và review chấp nhận nếu QO-2 duyệt; nếu Đạt chọn cách của review thì đổi `uow.py:60-61`,
hai port, năm chỗ gọi trong `src` và khoảng 25 chỗ gọi trong test. **Cần Đạt quyết ở QO-2.**

**5. Index dựng không `CONCURRENTLY` trên bảng phân vùng (minor): đúng về khóa; chấp nhận,
có ghi lại.** Docstring migration `cbf765d02a12` nay ghi khóa SHARE suốt lúc dựng, vì sao
chấp nhận hôm nay (plan không ghi môi trường triển khai nào có audit trail lớn; ai triển
khai lên một trail lớn thì kiểm trước) và cách làm không chặn ghi khi cần (`ON ONLY`, rồi
`CONCURRENTLY` từng phân vùng, rồi `ATTACH PARTITION`; với `approval_requests` thì tên tạm
rồi đổi tên). Không đổi DDL.

**8. Số đếm lệch (minor): đúng, đã sửa.** Dòng offboarding nay ghi 206 (lần chạy cuối, khớp
bảng lệnh); câu "12 guard" nay nói rõ là M1–M12 (Python), M13 (web) chạy riêng bằng vitest.

**Mutation vòng 1** (control xanh trước; khôi phục bytes và so sha256; `sha256` của
`0001_platform_baseline.sql` là `f1b2e5a8…c749c0` cả trước và sau):

| #   | Bỏ guard                                                   | Kết quả                                                                                                                                                                                           |
| --- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| MT1 | policy `tenant_isolation_approval_requests` `USING (true)` | **đỏ** 4: `test_another_tenants_approvals_are_not_there`, hai test chéo tenant của `test_approval_decider_scope.py`, `test_the_brief_holds_only_the_callers_tenant`; bản cũ của test đầu **xanh** |
| MT2 | policy `tenant_isolation_worker_runs` `USING (true)`       | **đỏ**: `test_worker_runs_visible_only_in_own_tenant`; bản cũ (workspace `0xB01`) **xanh**                                                                                                        |
| M14 | web: mục `/audit` của registry về `approvals.read`         | **đỏ** 2: vitest trang chủ và vitest menu tài khoản (một chủ cho cả hai cửa)                                                                                                                      |
| M15 | web: `session-chip.tsx` về `hasScope("approvals.read")`    | **đỏ**: `session-chip.test.tsx` "does not offer the audit log to a member who reads only the inbox"                                                                                               |

**Lệnh vòng 1 (6/10/2026, Windows, Git Bash, `.env` đã nạp):**

| Lệnh                                                           | Kết quả                                                                                                                                                                  |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `make ci`                                                      | xanh (exit 0): ruff, prettier, eslint (chỉ cảnh báo có sẵn), mypy 467 file, unit 1803 passed 3 skipped, import-linter 9 kept, contract 5, eval 4/4 và 22/22, manifest OK |
| `pytest -m integration packages/python/dw_platform/tests`      | 206 passed                                                                                                                                                               |
| `pytest -m integration packages/python/dw_agent_runtime/tests` | 61 passed                                                                                                                                                                |
| `pytest -m integration packages/python/dw_supply_chain/tests`  | 164 passed                                                                                                                                                               |
| `vitest run` home-page, session-chip, approvals-page           | 3 file, 9 passed                                                                                                                                                         |
| `alembic heads`                                                | một head `cbf765d02a12`                                                                                                                                                  |

Vòng này không đổi hợp đồng API (chỉ test, web, docstring migration), nên không sinh lại.

**Hạ tầng trong lúc làm:** khoảng 03:50 UTC cả compose `dw_elmichs` bị dừng từ ngoài phiên
này (`docker stop`, exit 137) và `dw_proterial` được bật. Đã `docker start` chỉ postgres,
valkey, qdrant, s3 của `dw_elmichs` để chạy kiểm và dọn dữ liệu xem trang, rồi dừng lại như
lúc thấy (keycloak, docgen không bật).
