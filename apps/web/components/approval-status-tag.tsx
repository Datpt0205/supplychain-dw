import type { Approval } from "@dw/contracts";
import { StatusTag, type StatusTone } from "@dw/ui";

/** One label and tone per approval status, for the inbox and an approval's page. */
const STATUS: Record<Approval["status"], { label: string; tone: StatusTone }> =
  {
    pending: { label: "Chờ quyết", tone: "warn" },
    approved: { label: "Đã duyệt", tone: "ok" },
    rejected: { label: "Từ chối", tone: "err" },
    cancelled: { label: "Đã hủy", tone: "gray" },
  };

export function ApprovalStatusTag({ status }: { status: Approval["status"] }) {
  return (
    <StatusTag tone={STATUS[status].tone}>{STATUS[status].label}</StatusTag>
  );
}
