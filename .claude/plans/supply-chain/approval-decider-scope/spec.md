# Giới hạn ai được quyết một approval (lát A)

Area: supply-chain · Nhánh: `main` · Viết: 5/10/2026

Phần chung, ứng viên đưa ngược. Quyết định: [ADR 0020](../../../../docs/adr/0020-e10-approval-decider-stamped-as-required-scope.md).

## Mục tiêu

Một approval có thể mang `required_scope`, đóng dấu lúc tạo; chỉ người có cả
`approvals.decide` và scope đó quyết được; trang `/approvals` đọc cùng dấu đó để khóa
nút kèm lý do.

## Hiện trạng (kiểm trong code ngày 5/10/2026)

- `ApproveAndResumeService.decide` (`dw_agent_runtime/approval_flow.py:96-140`) chỉ
  đòi `approvals.decide`; tiền tố nghiêm chỉ chặn tự duyệt và đòi nhận xét
  (`approval_flow.py:37-60`).
- `LangGraphWorkflowRunner._create_approval` (`adapters/langgraph_runner.py:698-713`)
  đọc `approval_type`, `reason` từ payload interrupt.
- `ApprovalRequest` (`dw_platform/domain/approval.py:44-58`) không có trường người quyết.

## Trong phạm vi

Ticket 01.

## Ngoài phạm vi

- Ẩn approval khỏi danh sách của người không quyết được.
- Duyệt nhiều bước trong một approval; chuỗi ký là nhiều approval nối nhau trong graph
  của context (`stage-1/issues/04`).

## Tiêu chí xong

Ticket 01 `resolved`; test âm của ticket xanh và mutation của nó ghi trong Comments.

## Danh sách ticket

| #   | Ticket                                                        | Status          | Blocked by                                                        |
| --- | ------------------------------------------------------------- | --------------- | ----------------------------------------------------------------- |
| 01  | [`required_scope` trên approval](issues/01-required-scope.md) | ready-for-agent | .claude/plans/supply-chain/port/issues/01-port-dw-supply-chain.md |
