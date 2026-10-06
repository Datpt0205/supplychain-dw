import type { SLAEvaluation } from "@dw/contracts";
import { StatusTag, type StatusTone } from "@dw/ui";

/**
 * The SLA milestones' names (CONTEXT.md "Mốc SLA"), the one place they are
 * written. The set is data (a tenant's SLA policy can name more), so an
 * unknown one shows as its own name.
 */
export const MILESTONE_LABEL: Record<string, string> = {
  deposit: "đặt cọc",
  port_arrival: "về cảng",
  payment: "thanh toán",
  warehouse_receipt: "nhập kho",
  bm04: "BM04",
  supplier_confirmation: "thống nhất với NCC",
};

export function milestoneLabel(milestone: string | null | undefined): string {
  return milestone ? (MILESTONE_LABEL[milestone] ?? milestone) : "";
}

const SLA: Record<
  SLAEvaluation["status"],
  { label: string; tone: StatusTone }
> = {
  not_applicable: { label: "Không áp dụng", tone: "gray" },
  // A milestone nobody confirmed raises no alert: unknown, never on time.
  not_evaluable: { label: "Chưa có chính sách xác nhận", tone: "unk" },
  on_track: { label: "Đúng tiến độ", tone: "ok" },
  breached: { label: "Trễ SLA", tone: "err" },
};

export function slaStatusLabel(status: SLAEvaluation["status"]): string {
  return SLA[status].label;
}

export function SlaStatusTag({ status }: { status: SLAEvaluation["status"] }) {
  return <StatusTag tone={SLA[status].tone}>{SLA[status].label}</StatusTag>;
}
