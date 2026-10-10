# 02 — Audit và trần chi tiêu cho ba lệnh ghi của Hồ sơ PO

Status: resolved
Blocked by: .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md
Area: supply-chain

## Mục tiêu

Hai chỗ code chuyển từ nhánh lưu trữ trái `CLAUDE.md`, đã kiểm ngày 5/10/2026 trên
cây làm việc sau port:

- **Không audit.** `CreatePOCase`, `SubmitSupplierUpdate`, `AnalyzeDelayImpact`
  (`application/handlers.py:161`, `:261`, `:344`) ghi một dòng mà không ghi dòng
  `platform.audit_events` nào; ba repository (`po_case_repository.py`,
  `supplier_update_repository.py`, `delay_impact_repository.py`) không nhắc tới audit.
  `CLAUDE.md`: side effect cần audit.
- **Gọi model không qua trần.** Mọi lời gọi model của Supply Chain đi qua
  `SingleCallModelGateway(inner=wiring.seam.gateway, ledger=wiring.seam.budget)`
  (`apps/api/src/dw_api/bootstrap/wiring.py:329`): không qua `RunAllowancePort` (quota
  của gói) và không qua trần chi tiêu ngày của tenant (`spend_guard.py`, chỉ được
  kiểm trong `langgraph_runner.py`). Đúng hình failure-modes #5: chặn ở một đường, đường
  kia vòng qua được.

## Việc cần làm

1. Audit trong cùng transaction của repository cho ba lệnh (mẫu
   `SeparationOfDutiesService`: repository ghi dòng và audit event cùng lúc); event
   mang tenant, workspace, actor, loại hành động, id hồ sơ; không mang nội dung tin NCC.
2. Một lớp bọc gateway (hoặc mở rộng `SingleCallModelGateway`) hỏi `RunAllowancePort`
   và trần chi tiêu ngày trước khi gọi `inner`; vượt thì từ chối với lỗi có tên, không
   gọi model. Đặt ở nền tảng (`dw_agent_runtime`), vì mọi context gọi một lần đều cần
   (ADR 0011: phần chung ở package nền tảng).
3. Quota cho lời gọi trực tiếp là quyết định Đạt còn nợ ("a plan quota on direct model
   calls", `.claude/PLAN.md`); ticket này dùng giới hạn gói hiện có, không tự đặt số.

## Tiêu chí chấp nhận

- [x] Integration: mỗi lệnh trong ba lệnh ghi đúng một dòng `platform.audit_events`
      cùng transaction; lệnh bị từ chối (NotFound, quyền) không ghi dòng nào.
- [x] Unit: tenant đã chạm trần ngày thì `SubmitSupplierUpdate` và
      `AnalyzeDelayImpact` bị từ chối và gateway bên trong không được gọi; tenant hết
      quota gói cũng vậy.
- [x] Mutation: bỏ kiểm trần trong lớp bọc thì test trên đỏ; bỏ ghi audit trong một
      repository thì test audit của lệnh đó đỏ (ghi vào Comments).
- [x] `reviewing-feature-security` chạy trên diff; kết quả trong Comments.

## Nguồn

- `CLAUDE.md`, "Non-negotiable architecture" (side effect cần audit) và "Agent and
  tool rules" (quota ở nơi run bắt đầu).
- `.claude/rules/failure-modes.md` #5.
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, mục "The `dw_supply_chain`
  package".

## Comments

**2026-10-07 (agent, Đạt giao quyết tạm):**

- **Phạm vi.** Ba lệnh của ticket, cộng mọi lệnh ghi `po_cases` còn thiếu audit
  khi grep: `AdvancePOCase` áp bước 11–17 trực tiếp, và nút `apply` của
  `advance_case_graph` (bước được duyệt rồi mới áp). Bước chờ duyệt không ghi gì
  (chưa xảy ra); nút `apply` ghi dưới người yêu cầu (context của run), `details`
  mang `run_id` để nối với quyết định mà hộp duyệt đã audit. Định dạng một chỗ:
  `application/po_case_audit.py` (`supply_chain.po_case.<action>`, resource
  `po_case`), lấy tenant/workspace/actor từ context, không từ entity;
  `ReassignPOCasePic` và `CreatePO` chuyển sang dùng nó. Hành động:
  `supply_chain.po_case.create|<case_action>`, `supply_chain.supplier_update.submit`
  (details: case, event_type, requires_confirmation — không có lời NCC),
  `supply_chain.delay_impact.analyze` (case, update, delay_days); cả hai mang
  `run_id` của lời gọi model.
- **Port bắt buộc `audit`, adapter cho `None`.** `POCaseRepositoryPort.add/save`,
  `SupplierUpdateRepositoryPort.add`, `DelayImpactAnalysisRepositoryPort.add`
  đòi `audit: AuditEvent` — handler hay graph nào gọi qua port mà quên thì mypy
  đỏ. Adapter SQL để mặc định `None` chỉ cho fixture/seed gọi thẳng (≈80 chỗ
  dựng dữ liệu test); như `save` vốn có.
- **Không làm lại phần trần.** Z4b đã đưa mọi lời gọi một lần qua
  `ModelStack.one_call(DailyAllowance(...))` (`SingleCallModelGateway`: quota run
  của gói và trần chi tiêu ngày, trước lời gọi), và
  `test_supply_chain_wiring.py` khẳng định cả bốn handler gọi model nhận lớp bọc
  đó. Ticket này thêm test ở mức handler: `SubmitSupplierUpdate` và
  `AnalyzeDelayImpact` bị từ chối (`QuotaExceededError`, `runs_per_day` và
  `spend_usd_per_day`), model trong không được gọi, không ghi dòng, không audit.
- **Còn mở, không thuộc ticket:** lượt quét follow-up mở/đóng follow-up vẫn ghi
  trên dòng, không vào `platform.audit_events` (không có actor người; nền tảng
  chưa có quy ước actor hệ thống) — vẫn trong mục "Open". Nháp đề xuất Zalo
  (`proposal_drafts`) không audit: trạng thái hội thoại tạm, có hạn và bị dọn;
  lần tiêu nháp được audit qua `propose`.
- **Mutation (đều đỏ rồi khôi phục):** bỏ `append(audit)` trong
  `SqlPOCaseRepository.add` → `test_creating_a_case_writes_one_audit_row_under_the_caller`;
  trong `save` → `test_a_step_applied_directly_writes_one_audit_row` và
  `test_pause_survives_restart_then_approval_applies_the_transition`; trong
  `SqlSupplierUpdateRepository.add` / `SqlDelayImpactAnalysisRepository.add` →
  `test_a_supplier_update_and_its_analysis_each_write_one_audit_row`; bỏ
  `allowance.require` trong `SingleCallModelGateway` → bốn test
  `*_refused_before_the_model_once_the_plan_day_is_spent`.
- **reviewing-feature-security.** (1) Tenant: audit ghi trong phiên tenant của
  lệnh (RLS `WITH CHECK` của `audit_events`); test: tenant khác gọi advance/submit
  lên hồ sơ của A → `NotFoundError`, không dòng audit nào mang tenant của nó, dòng
  của hồ sơ chỉ mang tenant A. (2) Authz: không đổi; lệnh bị từ chối quyền không
  ghi audit (unit). (3) Autonomy: không thêm tầm với; nút `apply` chỉ thêm bản ghi.
  (4) Nội dung không tin cậy: lời NCC không vào audit (test kiểm). (5) Audit cùng
  transaction: tạo hồ sơ trùng số PO bị DB từ chối → không còn dòng audit. (6)
  Trần kiểm trước lời gọi, ledger dọn trong `finally` (có sẵn).
