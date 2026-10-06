# 01 — `required_scope` trên approval: đóng dấu lúc tạo, kiểm lúc quyết, đọc ở `/approvals`

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

Chỉ người giữ scope đã đóng dấu trên approval mới quyết được nó (spec, Mục tiêu). Ứng
viên đưa ngược. Bị chặn bởi port chỉ để migration nối sau head của chuỗi sản phẩm.

## Việc cần làm

1. **Migration nền tảng** (id hex ngẫu nhiên): `platform.approval_requests` thêm
   `required_scope text NULL` với CHECK dạng tên scope
   (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`). `ApprovalRequest` và repository thêm trường.
2. **Đóng dấu:** `_create_approval` đọc `payload.get("required_scope")`; giá trị không
   khớp CHECK làm run lỗi, không bỏ qua (đóng khi sai, failure-modes #7). Ghi docstring:
   payload do node viết từ policy, không bao giờ từ output mô hình.
3. **Kiểm:** trong `decide`, khi `approve` hoặc khi người quyết không phải người yêu
   cầu, sau `approvals.decide`, nếu `required_scope` khác NULL thì
   `authorization.require(action=required_scope, ...)`. Kiểm trước khi ghi quyết định
   và trước `runner.resume`.
4. **API:** `GET /approvals` và `GET /approvals/{id}` trả `required_scope`; contracts
   TypeScript thêm trường.
5. **Web:** `/approvals` khóa nút quyết khi người xem thiếu `required_scope`, với lý
   do bằng chữ trong `Tooltip` và cạnh nút ("Chỉ người có quyền … được quyết yêu cầu
   này"); không tự suy quyền từ tiền tố.

## Tiêu chí chấp nhận

- [x] **Test âm (unit service):** người có `approvals.decide` mà thiếu
      `required_scope` gọi duyệt thì `PermissionDeniedError`; approval vẫn `pending`;
      `runner.resume` không được gọi; không có dòng decision.
- [x] Cùng người khi có scope: duyệt được. `required_scope` NULL: hành vi như hôm nay
      (test có sẵn vẫn xanh). Người yêu cầu rút yêu cầu của mình không cần scope.
- [x] **Test âm (integration, DB thật):** approval của tenant khác trả not found, kể cả
      với người có scope. _(Sửa 5/10/2026, quyết định tạm 1: "hoặc workspace khác" chuyển
      sang `.claude/plans/platform-runtime/approval-audit-and-workspace/issues/02`; RLS
      approval vẫn chỉ lọc tenant.)_
- [x] Payload interrupt có `required_scope` sai dạng: run lỗi, không tạo approval.
- [x] Mutation: bỏ lệnh kiểm ở bước 3 thì test âm đỏ (ghi vào Comments). Kiểm ở web bị
      bỏ thì API vẫn từ chối (test API trên).
- [x] Vitest `/approvals`: người thiếu scope thấy nút khóa kèm lý do; người có scope
      thấy nút bật.
- [x] `make ci` xanh; openapi sinh lại. _(Vòng 2: `make ci` xanh sau khi chạy prettier
      trên bảng của `.claude/plans/supply-chain.md`, chỉ đổi khoảng trắng; xem Comments.)_

## Nguồn

- `docs/products/elmich/surveys/2026-10-05-pdf-gap.md` mục 3, "Not yet solved" (không có luật "chỉ BGĐ quyết loại X").
- `docs/products/elmich/surveys/2026-10-05-synthesis.md` mục 4, "Platform gap".
- Trên `main`: `approval_flow.py:96-140`, `langgraph_runner.py:698-713`,
  `domain/approval.py:44-58`.

## Comments

### 2026-10-05 — lát A đã làm (implementer); quyết định tạm của lead, chờ Đạt duyệt ở QO-2

**Quyết định tạm (bắt buộc cho lát này), mỗi cái đã làm:**

1. **Khác workspace ra khỏi A.** Thuộc platform-runtime/approval-audit-and-workspace/02
   (lọc ở repository, không đổi RLS; đổi RLS approval sang dạng workspace là quyết định
   riêng). Tiêu chí chấp nhận đã sửa ở trên; ADR 0020 có đoạn "Sửa đổi 2026-10-05" và
   bullet ở Hệ quả. Không đổi policy RLS của `approval_requests`/`approval_decisions`.
   Chú thích sai "tenant/workspace-scoped by RLS" ở `approval_flow.py` để lại cho 02;
   lệnh kiểm của A không dựa vào nó.
2. **`platform_admin` qua được `required_scope`** vì đi qua cùng
   `authorization.require` (luật admin của `ScopeAuthorizationService.is_allowed`). Ghim
   bằng `test_platform_admin_passes_the_stamped_scope_through_the_same_rule`; ghi vào
   Hệ quả của ADR 0020.
3. **Web:** `Tooltip` của antd import thẳng (như `zalo-connect-card.tsx`), nút bị khóa
   bọc trong `span`, lý do hiện cả bằng chữ cạnh nút: "Chỉ người có quyền `<scope>` được
   quyết yêu cầu này". Người yêu cầu giữ Reject (rút) trên yêu cầu của mình. Khóa đọc
   `approval.required_scope` và `hasScope` của phiên, không suy từ tiền tố. **Không làm
   lại phần còn lại của `/approvals`** (shadcn, chữ tiếng Anh): đó là ticket W. Riêng
   dòng lý do là code mới của lát này nên dùng `Typography.Text type="secondary"` của
   antd (màu và cỡ chữ từ theme), không dùng Tailwind cho màu/chữ. Tồn tại từ trước và để cho W: người yêu cầu KHÔNG có
   `approvals.decide` vẫn chỉ thấy trang ở chế độ đọc, không có nút rút, dù server cho rút.
4. **Dạng tên scope có một chủ: CHECK** `ck_approval_requests_required_scope`
   (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$`). Không có kiểm tên scope ở tầng domain để dùng
   lại (đã tìm). `_create_approval` chuyển `payload.get("required_scope")` nguyên vẹn.
   Phát hiện khi làm: `start` và `resume` gọi `_handle_outcome` ngoài khối bắt lỗi của
   chúng, nên INSERT bị từ chối thoát ra khỏi `start` và run kẹt `running` (đã thấy đỏ
   trước khi sửa). Sửa: lỗi ở `_create_approval` gọi `_fail` rồi dừng, như ca "một bước
   dừng ở hai approval" sẵn có. Chuỗi rỗng, chữ hoa, một đoạn, số, list: đều làm run
   `failed`, không dòng approval (test với Postgres thật trên đường `start`; đường
   `stream` thử một giá trị sai dạng, chữ hoa, qua cùng `_handle_outcome`). `error` của
   run là `InfrastructureError` "the approval request could not be recorded"; lỗi gốc
   của driver chỉ vào log server (xem vòng 1 bên dưới).
5. **Repository:** `required_scope` chỉ do `add()` ghi. Có test `save()` sau khi quyết
   không đổi dấu (kể cả khi aggregate bị sửa trong bộ nhớ). Có làm phần tùy chọn: Postgres
   không thu được một cột khỏi UPDATE toàn bảng, nên migration thu UPDATE toàn bảng của
   `dw_app` và cấp lại UPDATE (`status`, `decided_at`, `version`), đúng ba cột `save` ghi;
   `test_privileges.py::test_the_application_may_only_record_a_decision_on_an_approval`
   khẳng định từ catalog; test UPDATE thật bị `permission denied` là
   `test_approval_stamp_column.py::test_the_application_role_cannot_rewrite_the_stamp`.
6. **Một mapper `_view`** thay ba lần dựng `ApprovalView`; thêm `required_scope: str |
None`. Đã chạy `make generate-contracts` (openapi + `platform.d.ts`) và thêm trường vào
   `approvalSchema` (zod) ở `packages/typescript/contracts/src/runs.ts`.
7. **Không có suite integration của API:** test âm chéo tenant nằm ở
   `packages/python/dw_agent_runtime/tests/integration/test_approval_decider_scope.py`,
   chạy `ApproveAndResumeService.decide` trên Postgres thật với runner thật. Thêm test
   unit route `apps/api/tests/unit/test_approvals_endpoint.py`: gọi thẳng HTTP, không có
   trang, `approvals.decide` thiếu dấu thì 403 và không ghi gì.
8. Đã ghi vào Comments của `zalo-channel/issues/05`: Z5 phải quyết qua
   `ApproveAndResumeService.decide`.
9. Migration `5d3965984679` (hex của alembic), `down_revision = cf66605631d7`; một head.
   Downgrade bỏ CHECK, cột, trả lại UPDATE toàn bảng; đã chạy downgrade -1 rồi upgrade
   head trên DB thử: cột 1→0→1, CHECK 1→0→1, UPDATE toàn bảng của `dw_app` False→True→False.

**Lệch khỏi ticket/quyết định:** API trả thêm `requested_by_me: bool` (không có trong
quyết định 6). Cần để giữ Reject cho người yêu cầu (quyết định 3) khi trang không biết yêu
cầu nào của người xem; là boolean tính ở server (như `mine` của follow-up), không lộ id
thành viên khác. Ghi ở ADR 0020, Sửa đổi điểm 3.

**Đi qua `reviewing-feature-security`:**

- Tenant: không bảng mới; cột mới trên bảng có RLS FORCE sẵn. Test âm: tenant B giữ cả hai
  scope quyết approval của tenant A thì `NotFoundError` **của approval** ("approval
  request not found", `details.approval_id`), approval `pending`, 0 dòng decision, run vẫn
  `waiting_approval`; và cùng điều đó với approval không có run (vòng 2). Khác workspace:
  ticket 02.
- Phân quyền: kiểm ở nơi ghi quyết định (`decide`), trước mọi ghi và trước `resume`, nên
  platform-runtime/01 (audit `approval.decided`) ghép vào sau mà lần từ chối vì thiếu dấu
  không có dòng audit. Dấu đọc từ dòng, không suy lại từ policy. Danh tính và scope từ
  `AccessContext` server. Test âm gọi thẳng HTTP (403).
- Autonomy/approval: không thêm tầm với nào cho agent. Mô hình không đặt được dấu: tham số
  của tool đi dưới `payload["payload"]`; test
  `test_a_model_cannot_stamp_who_may_decide` (tool có tham số tên `required_scope`).
- Nội dung không tin cậy: đầu ra mô hình không thành quyền (test trên).
- Audit/provenance: audit của quyết định là platform-runtime/01, không làm ở đây.
- Mặc định: NULL = luật hôm nay (quyết định của ADR). Sai dạng hoặc không phải chuỗi:
  đóng (run `failed`), không ép, không bỏ.
- Phản hồi API thêm hai trường: tên scope (thông tin danh mục, không bí mật) và một
  boolean về chính người gọi; không đường nào phụ thuộc môi trường.

**Lệnh đã chạy (5/10/2026, Windows, Git Bash):**

| Lệnh                                                                  | Kết quả                                                                                                                                                                                            |
| --------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `make lint`                                                           | **đỏ**: ruff check/format xanh (607 file); `pnpm run format:check` báo `.claude/plans/supply-chain.md` (commit 91b74fd, đã đỏ ở HEAD, không phải file của lát này); eslint web chỉ cảnh báo có sẵn |
| `make typecheck`                                                      | xanh: mypy 443 file, tsc                                                                                                                                                                           |
| `make test-unit`                                                      | xanh: 1467 passed, 3 skipped                                                                                                                                                                       |
| `make test-architecture`                                              | xanh: import-linter 9 kept, 0 broken; declared-dependency 12 gói; invariants ok                                                                                                                    |
| `make test-contract`                                                  | xanh: 2 passed (đỏ khi trả `openapi.json` về HEAD: 1 failed)                                                                                                                                       |
| `make generate-contracts`                                             | đã chạy; `platform.d.ts` thêm `required_scope`, `requested_by_me`                                                                                                                                  |
| `make release-manifest-check`                                         | xanh (không thêm config có phiên bản)                                                                                                                                                              |
| `make eval-smoke`                                                     | xanh: platform 4/4, supply_chain 22/22                                                                                                                                                             |
| `pytest -m integration packages/python/dw_platform/tests` (.env)      | xanh: 199                                                                                                                                                                                          |
| `pytest -m integration packages/python/dw_agent_runtime/tests` (.env) | xanh: 56                                                                                                                                                                                           |
| `pytest -m integration packages/python/dw_supply_chain/tests` (.env)  | xanh: 93                                                                                                                                                                                           |
| `pnpm --filter @dw/web exec vitest run`                               | xanh: 18 file, 93 test (trong đó `app/approvals/__tests__/approvals-page.test.tsx`: 5)                                                                                                             |

`make ci` không chạy integration và vitest; hai thứ đó chạy riêng như trên.

**Mutation (mỗi ca: control xanh, sửa có kiểm đã áp dụng, chạy, khôi phục, `git diff` cho
thấy guard đã về):**

| #    | Bỏ guard                                                           | Test đỏ                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1   | `if request.required_scope is not None: require(...)` → `if False` | unit `test_the_decide_right_alone_cannot_decide_a_stamped_request[approve,reject]`, `test_the_requester_cannot_approve_their_own_stamped_request_without_the_scope`; API `test_the_server_refuses_a_decision_the_page_would_have_locked`; integration `test_the_stamp_decides_who_may_decide`                                                                                                             |
| M2   | runner không bắt lỗi của `_create_approval`                        | integration `test_a_malformed_stamp_fails_*`                                                                                                                                                                                                                                                                                                                                                              |
| M3   | `payload.get("required_scope") or None`                            | integration, ca chuỗi rỗng                                                                                                                                                                                                                                                                                                                                                                                |
| M4   | bỏ CHECK trong migration                                           | `test_approval_stamp_column.py` (các ca sai dạng)                                                                                                                                                                                                                                                                                                                                                         |
| M5   | giữ UPDATE toàn bảng cho `dw_app`                                  | `test_privileges.py::test_the_application_may_only_record_a_decision_on_an_approval`, `test_approval_stamp_column.py::test_the_application_role_cannot_rewrite_the_stamp`                                                                                                                                                                                                                                 |
| M6   | `save()` ghi cả `required_scope`                                   | `test_a_decision_does_not_move_the_stamp`                                                                                                                                                                                                                                                                                                                                                                 |
| M7   | `_ask_human` trải tham số tool ra cấp trên của interrupt           | `test_a_model_cannot_stamp_who_may_decide`                                                                                                                                                                                                                                                                                                                                                                |
| M8   | `requested_by_me=False`                                            | `test_approvals_endpoint.py`                                                                                                                                                                                                                                                                                                                                                                              |
| M9   | web: Approve không khóa theo dấu                                   | vitest `approvals-page.test.tsx`                                                                                                                                                                                                                                                                                                                                                                          |
| M10  | web: khóa cả Reject của người yêu cầu                              | vitest `approvals-page.test.tsx`                                                                                                                                                                                                                                                                                                                                                                          |
| M11  | web: bỏ lý do bằng chữ cạnh nút                                    | vitest `approvals-page.test.tsx`                                                                                                                                                                                                                                                                                                                                                                          |
| M12  | runner lưu lỗi gốc của driver vào `worker_runs.error`              | integration `test_a_malformed_stamp_fails_*` (6 ca, `_assert_no_driver_text`)                                                                                                                                                                                                                                                                                                                             |
| M15  | tắt RLS (NO FORCE + DISABLE) chỉ trên `platform.approval_requests` | vòng 1: `test_the_stamp_decides_who_may_decide` **vẫn xanh** (NotFoundError đến từ `run not found`). Vòng 2: đỏ ở `test_the_stamp_decides_who_may_decide` (`'run not found' == 'approval request not found'`) và `test_another_tenant_cannot_decide_a_stamped_approval_without_a_run` (tenant B qua cả hai kiểm scope, chỉ bị RLS của `approval_decisions` chặn ở INSERT); `test_rls_coverage.py` cũng đỏ |
| M15b | tắt RLS trên cả `approval_requests` và `worker_runs`               | integration `test_the_stamp_decides_who_may_decide` (vòng 1)                                                                                                                                                                                                                                                                                                                                              |

Kiểm ở web bị bỏ (M9) thì API vẫn từ chối: M1 cho thấy test API là thứ giữ điều đó.

**Vòng 1 review (5/10/2026):**

- _security, `langgraph_runner.py` `_handle_outcome`:_ đúng. Lỗi gốc của INSERT bị từ
  chối (`IntegrityError ... [SQL: INSERT INTO platform.approval_requests ...`) đi thẳng vào
  `worker_runs.error`, và `GET /runs/{id}` trả nguyên. Test trước: `_assert_no_driver_text`
  trong cả hai test sai dạng (không `[SQL:`, `INSERT INTO`, `approval_requests`, `ck_`,
  `asyncpg`, `sqlalchemy`) đỏ 6/6 trước khi sửa. Sửa: `logger.exception` ở server, `_fail`
  nhận `InfrastructureError("the approval request could not be recorded")` (theo
  `dw_kernel.errors`: adapter bọc lỗi hạ tầng, không để lỗi SDK thô vượt biên). Không phân
  biệt CHECK với DB sập ở đây (runner không import SQLAlchemy); log giữ nguyên nhân. Ngoài
  lát này: `str(exc)` vẫn được lưu ở ba chỗ có sẵn khi graph lỗi (`start`, chạy
  `stream`, `resume`; `langgraph_runner.py` 356, 515, 598); không sửa, ghi lại để xét
  riêng.
- _standards + coverage, `page.tsx` dòng lý do:_ đúng (hai phát hiện là một). Đổi sang
  `Typography.Text type="secondary"`; vitest 5/5; chạy lại M11 trên phần tử mới: 2 test đỏ,
  khôi phục.
- _coverage, M5 và điểm 5:_ đúng; đã ghi rõ file của từng test.
- _coverage, điểm 4:_ đúng; đã sửa câu (năm ca trên `start`, một ca trên `stream`).

Chạy lại sau vòng 1: `make lint` vẫn đỏ chỉ vì `.claude/plans/supply-chain.md` (ruff
check/format xanh 607 file; prettier xanh cho file của lát này; eslint chạy riêng
`pnpm run -r --if-present lint`: xanh). `make typecheck` xanh (mypy 443, tsc).
`make test-unit` 1467 passed, 3 skipped. `make test-architecture` 9 kept, 0 broken,
invariants ok. `make test-contract` 2 passed (API không đổi ở vòng này nên không sinh lại
contracts). `make release-manifest-check` OK. Integration (.env): `dw_agent_runtime` 56,
`dw_platform` 199, `dw_supply_chain` 93, đều passed. Vitest web: 18 file, 93 test passed.

**Vòng 2 review (5/10/2026):**

- _Test âm chéo tenant không thấy được lần đọc approval (failure-modes #3):_ đúng, đã kiểm
  lại trong code. `decide` có hai `NotFoundError`: của approval (`approval_flow.py`,
  "approval request not found") và của run (`run_store.py`, "run not found", qua RLS của
  `worker_runs` trong `_resumable_run`). Test chỉ có `pytest.raises(NotFoundError)` nên
  khi tắt RLS của `approval_requests` (M15) nó vẫn xanh nhờ lỗi của run. Sửa test (không
  sửa code; hành vi đúng): `_assert_the_approval_was_not_found` khẳng định `message` và
  `details == {"approval_id": ...}`; thêm
  `test_another_tenant_cannot_decide_a_stamped_approval_without_a_run` (approval có dấu,
  `run_id=None`, thêm qua `uow.approvals.add`), nơi lần đọc approval là chốt duy nhất:
  tenant B giữ cả hai scope thì not found của approval, approval `pending`, `version` 1,
  0 dòng decision. Control xanh (9/9), áp M15 bằng sửa tạm migration `5d3965984679`
  (DB thử được tạo lại và migrate mỗi phiên): cả hai test đỏ (lý do ở bảng mutation);
  khôi phục, sha256 của migration trùng bản sao lưu.
- _`make ci` đỏ ở prettier trên `.claude/plans/supply-chain.md`:_ đúng (file không đổi
  từ 91b74fd; prettier chỉ đòi căn cột bảng "Slice | Commit | What"). **Ngoài lát này:**
  đã chạy `npx prettier --write` trên đúng file đó; `git diff -w` chỉ còn dòng phân cách
  của bảng dài thêm dấu `-`, nội dung không đổi. Lead có thể bỏ thay đổi này nếu muốn tự
  sửa; khi đó `make ci` lại đỏ ở cùng chỗ.

Chạy lại sau vòng 2 (5/10/2026): `make ci` **xanh** (exit 0, ">> local CI gate passed"):
ruff check xanh, ruff format 607 file, prettier "All matched files use Prettier code
style", eslint Done; mypy 443 file không lỗi, tsc Done (contracts, ui, api-client, web);
unit 1467 passed, 3 skipped; import-linter 9 kept, 0 broken, declared-dependency 12 gói;
test-contract 2 passed; eval-smoke platform 4/4, supply_chain 22/22; release manifest OK.
Integration (.env): `dw_agent_runtime` 57 passed (56 + test mới), `dw_platform` 199 passed,
`dw_supply_chain` 93 passed. Vitest web: 18 file, 93 test passed.

**Còn thiếu để `resolved`:** không còn tiêu chí nào mở; đánh dấu `resolved` ở file area là
việc của lead (kèm duyệt QO-2 cho các quyết định tạm). Ghi chú thêm: DB dev
cục bộ (`dw_elmichs`) chưa chạy `make db-migrate` (đang ở `cf66605631d7`); API chạy từ code
này cần migrate trước. Trên Windows `make generate-contracts` ghi `openapi.json` bằng CRLF
(git chuẩn hóa về LF; đã đổi lại LF trong working copy).

- 2026-10-05, lead: verifier độc lập chạy lại toàn bộ (lint, typecheck, unit 1467, architecture, contract, release manifest, integration dw_platform 199 / dw_agent_runtime 57 / dw_supply_chain 93, vitest 93, một head `5d3965984679`) và 24 mutation, tất cả đỏ rồi khôi phục. `make ci` đỏ trước đó chỉ vì prettier trên area file của lead, đã sửa. Còn lại cho lát W: chữ lý do khóa dùng `text-muted-foreground` như phần còn lại của trang shadcn.
- 2026-10-05, platform-runtime/approval-audit-and-workspace/02: phần "hoặc workspace khác" của tiêu chí gốc (đã chuyển khỏi lát này ở quyết định tạm 1) nay được phủ: `packages/python/dw_agent_runtime/tests/integration/test_approval_workspace.py::test_another_workspace_neither_sees_nor_decides_the_approval` và `::test_another_workspace_cannot_decide_an_approval_without_a_run` (cùng tenant, W2 giữ `approvals.decide` quyết approval của W1: not found của approval, vẫn `pending`, 0 dòng decision, run không chạy tiếp). Lọc ở repository, RLS không đổi; ADR 0020 có đoạn sửa đổi thứ hai.
- 2026-10-06, platform-runtime/approval-audit-and-workspace/02, review vòng 1: hai test chéo tenant của lát này (`test_approval_decider_scope.py::test_the_stamp_decides_who_may_decide` và `::test_another_tenant_cannot_decide_a_stamped_approval_without_a_run`) nay cho tenant kia mang đúng workspace id của approval, để bộ lọc workspace của ticket 02 không thay được ranh giới tenant. Đổi policy `tenant_isolation_approval_requests` thành `USING (true)` thì cả hai đỏ; bản cũ (workspace khác) thì xanh.
