import type { SupplierEventType } from "@dw/contracts";
import { StatusTag, type StatusTone } from "@dw/ui";

const SUPPLIER_EVENT: Record<
  SupplierEventType,
  { label: string; tone: StatusTone }
> = {
  production_delay: { label: "Trễ sản xuất", tone: "warn" },
  qc_issue: { label: "Sự cố QC", tone: "warn" },
  shipment_update: { label: "Cập nhật vận chuyển", tone: "pri" },
  deposit_confirmation: { label: "Xác nhận đặt cọc", tone: "ok" },
  document_submitted: { label: "Đã nộp chứng từ", tone: "gray" },
  no_official_update: { label: "Chưa có cập nhật chính thức", tone: "outline" },
  other: { label: "Khác", tone: "outline" },
};

export function SupplierEventTag({ type }: { type: SupplierEventType }) {
  return (
    <StatusTag tone={SUPPLIER_EVENT[type].tone}>
      {SUPPLIER_EVENT[type].label}
    </StatusTag>
  );
}
