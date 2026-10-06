import type { MissingUpdateStatus } from "@dw/contracts";
import { StatusTag, type StatusTone } from "@dw/ui";

const MISSING_UPDATE: Record<
  MissingUpdateStatus["status"],
  { label: string; tone: StatusTone }
> = {
  on_track: { label: "Có cập nhật gần đây", tone: "ok" },
  reminder_due: { label: "Cần nhắc NCC", tone: "warn" },
  escalation_due: { label: "Cần leo thang", tone: "err" },
};

export function missingUpdateLabel(
  status: MissingUpdateStatus["status"],
): string {
  return MISSING_UPDATE[status].label;
}

export function MissingUpdateTag({
  status,
}: {
  status: MissingUpdateStatus["status"];
}) {
  return (
    <StatusTag tone={MISSING_UPDATE[status].tone}>
      {MISSING_UPDATE[status].label}
    </StatusTag>
  );
}
