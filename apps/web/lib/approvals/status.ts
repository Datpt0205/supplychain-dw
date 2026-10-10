import type { Approval } from "@dw/contracts";
import type { StatusTone } from "@dw/ui";

/** An approval's status as a person reads it: the one label table, used by
 * the inbox and the approval's own page alike (`ApprovalStatusTag`). */
export const APPROVAL_STATUS: Record<
  Approval["status"],
  { label: string; tone: StatusTone }
> = {
  pending: { label: "Chờ quyết", tone: "warn" },
  approved: { label: "Đã duyệt", tone: "ok" },
  rejected: { label: "Từ chối", tone: "err" },
  cancelled: { label: "Đã hủy", tone: "gray" },
};
