import type { CaseState } from "@dw/contracts";
import { Badge, type BadgeProps } from "@dw/ui";

/** Vietnamese label + badge tone for each `CaseState` the backend can return.
 * One place: a new state added on the Python side shows up here as a plain
 * fallback (the raw value) rather than breaking the page. */
export const CASE_STATE_LABEL: Record<CaseState, string> = {
  po_created: "Đã tạo PO",
  waiting_deposit: "Chờ đặt cọc",
  deposit_confirmed: "Đã xác nhận cọc",
  pre_production: "Chuẩn bị sản xuất",
  production: "Đang sản xuất",
  qc: "Kiểm tra chất lượng",
  in_transit: "Đang vận chuyển",
  arrived_port: "Đã đến cảng",
  waiting_payment: "Chờ thanh toán",
  payment_completed: "Đã thanh toán",
  warehouse_receiving: "Đang nhập kho",
  completed: "Hoàn tất",
  waiting_external: "Chờ bên ngoài",
  blocked: "Đang bị chặn",
  rework: "Làm lại",
  manual_review: "Cần xem xét thủ công",
  cancelled: "Đã hủy",
};

const STATE_VARIANT: Record<CaseState, BadgeProps["variant"]> = {
  po_created: "secondary",
  waiting_deposit: "default",
  deposit_confirmed: "default",
  pre_production: "default",
  production: "default",
  qc: "default",
  in_transit: "default",
  arrived_port: "default",
  waiting_payment: "default",
  payment_completed: "default",
  warehouse_receiving: "default",
  completed: "success",
  waiting_external: "warning",
  blocked: "warning",
  rework: "warning",
  manual_review: "warning",
  cancelled: "destructive",
};

export function CaseStateBadge({ state }: { state: CaseState }) {
  return (
    <Badge variant={STATE_VARIANT[state]}>
      {CASE_STATE_LABEL[state] ?? state}
    </Badge>
  );
}
