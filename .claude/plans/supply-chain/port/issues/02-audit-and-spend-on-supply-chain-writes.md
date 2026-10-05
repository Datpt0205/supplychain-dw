# 02 — Audit và trần chi tiêu cho ba lệnh ghi của Hồ sơ PO

Status: ready-for-agent
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

- [ ] Integration: mỗi lệnh trong ba lệnh ghi đúng một dòng `platform.audit_events`
      cùng transaction; lệnh bị từ chối (NotFound, quyền) không ghi dòng nào.
- [ ] Unit: tenant đã chạm trần ngày thì `SubmitSupplierUpdate` và
      `AnalyzeDelayImpact` bị từ chối và gateway bên trong không được gọi; tenant hết
      quota gói cũng vậy.
- [ ] Mutation: bỏ kiểm trần trong lớp bọc thì test trên đỏ; bỏ ghi audit trong một
      repository thì test audit của lệnh đó đỏ (ghi vào Comments).
- [ ] `reviewing-feature-security` chạy trên diff; kết quả trong Comments.

## Nguồn

- `CLAUDE.md`, "Non-negotiable architecture" (side effect cần audit) và "Agent and
  tool rules" (quota ở nơi run bắt đầu).
- `.claude/rules/failure-modes.md` #5.
- `docs/products/elmich/surveys/2026-10-05-archive-port.md`, mục "The `dw_supply_chain`
  package".

## Comments
