import type { SLAEvaluation } from "@dw/contracts";
import { Badge, type BadgeProps } from "@dw/ui";

const LABEL: Record<SLAEvaluation["status"], string> = {
  not_applicable: "Không áp dụng",
  not_evaluable: "Chưa có chính sách xác nhận",
  on_track: "Đúng tiến độ",
  breached: "Trễ SLA",
};

const VARIANT: Record<SLAEvaluation["status"], BadgeProps["variant"]> = {
  not_applicable: "secondary",
  not_evaluable: "outline",
  on_track: "success",
  breached: "destructive",
};

export function SlaStatusBadge({
  status,
}: {
  status: SLAEvaluation["status"];
}) {
  return <Badge variant={VARIANT[status]}>{LABEL[status]}</Badge>;
}
