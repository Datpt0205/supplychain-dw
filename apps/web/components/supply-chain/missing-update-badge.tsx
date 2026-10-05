import type { MissingUpdateStatus } from "@dw/contracts";
import { Badge, type BadgeProps } from "@dw/ui";

const LABEL: Record<MissingUpdateStatus["status"], string> = {
  on_track: "Có cập nhật gần đây",
  reminder_due: "Cần nhắc nhở nhà cung cấp",
  escalation_due: "Cần leo thang",
};

const VARIANT: Record<MissingUpdateStatus["status"], BadgeProps["variant"]> = {
  on_track: "success",
  reminder_due: "warning",
  escalation_due: "destructive",
};

export function MissingUpdateBadge({
  status,
}: {
  status: MissingUpdateStatus["status"];
}) {
  return <Badge variant={VARIANT[status]}>{LABEL[status]}</Badge>;
}
