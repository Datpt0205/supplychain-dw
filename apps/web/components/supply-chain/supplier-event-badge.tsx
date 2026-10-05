import type { SupplierEventType } from "@dw/contracts";
import { Badge, type BadgeProps } from "@dw/ui";

const LABEL: Record<SupplierEventType, string> = {
  production_delay: "Trễ sản xuất",
  qc_issue: "Sự cố QC",
  shipment_update: "Cập nhật vận chuyển",
  deposit_confirmation: "Xác nhận đặt cọc",
  document_submitted: "Đã nộp chứng từ",
  no_official_update: "Chưa có cập nhật chính thức",
  other: "Khác",
};

const VARIANT: Record<SupplierEventType, BadgeProps["variant"]> = {
  production_delay: "warning",
  qc_issue: "warning",
  shipment_update: "default",
  deposit_confirmation: "success",
  document_submitted: "secondary",
  no_official_update: "outline",
  other: "outline",
};

export function SupplierEventBadge({ type }: { type: SupplierEventType }) {
  return <Badge variant={VARIANT[type]}>{LABEL[type]}</Badge>;
}
