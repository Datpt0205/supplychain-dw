import type { Approval } from "@dw/contracts";
import { StatusTag } from "@dw/ui";
import { APPROVAL_STATUS } from "../lib/approvals/status";

export function ApprovalStatusTag({ status }: { status: Approval["status"] }) {
  const { label, tone } = APPROVAL_STATUS[status];
  return <StatusTag tone={tone}>{label}</StatusTag>;
}
