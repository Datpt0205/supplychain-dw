import type { CaseState, OrderKind } from "@dw/contracts";
import { StatusTag, type StatusTone } from "@dw/ui";

/** Vietnamese label for each `CaseState` the backend can return. The one
 * owner of these words (CONTEXT.md "Trạng thái của Hồ sơ PO" copies it); a
 * state added on the Python side fails the type check here first. */
/** The first history row of a case the one-time import brought in
 * (onboarding/02): it has no state before it. */
export const IMPORTED_LABEL = "Nạp từ dữ liệu cũ";

export const CASE_STATE_LABEL: Record<CaseState, string> = {
  order_requested: "Chờ tạo PO",
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

/**
 * Each state's step of the 17 (CONTEXT.md's notes column; null for an end or
 * an interruption) and its tone. A step in progress is blue; only an
 * interruption asks for attention, and colour never carries the meaning
 * alone: the label does.
 */
export const CASE_STATE_META: Record<
  CaseState,
  { step: number | null; tone: StatusTone }
> = {
  order_requested: { step: 10, tone: "pri" },
  po_created: { step: 10, tone: "pri" },
  waiting_deposit: { step: 11, tone: "pri" },
  deposit_confirmed: { step: 11, tone: "pri" },
  pre_production: { step: 12, tone: "pri" },
  production: { step: 13, tone: "pri" },
  qc: { step: 14, tone: "pri" },
  in_transit: { step: 14, tone: "pri" },
  arrived_port: { step: 15, tone: "pri" },
  waiting_payment: { step: 16, tone: "pri" },
  payment_completed: { step: 16, tone: "pri" },
  warehouse_receiving: { step: 17, tone: "pri" },
  completed: { step: null, tone: "ok" },
  waiting_external: { step: null, tone: "gold" },
  blocked: { step: null, tone: "err" },
  rework: { step: 13, tone: "warn" },
  manual_review: { step: null, tone: "warn" },
  cancelled: { step: null, tone: "gray" },
};

/** Step 10's classification of the order (`OrderKind`). */
export const ORDER_KIND_LABEL: Record<OrderKind, string> = {
  new: "Hàng mới",
  reorder: "Hàng đặt lại",
};

/** "Bước 11 · Giai đoạn 2": where a step sits in the 17. */
export function stepLabel(step: number | null): string | null {
  if (step === null) return null;
  return `Bước ${step} · Giai đoạn ${step <= 9 ? 1 : 2}`;
}

export function CaseStateTag({ state }: { state: CaseState }) {
  return (
    <StatusTag tone={CASE_STATE_META[state]?.tone ?? "gray"}>
      {CASE_STATE_LABEL[state] ?? state}
    </StatusTag>
  );
}
