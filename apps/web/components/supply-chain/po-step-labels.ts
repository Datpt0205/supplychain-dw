/** Words of the PO step card (tickets ai-automation/15-18), shared with its tests. */
import type { POStepKind } from "@dw/contracts";

export const OFFLINE_STEP = "Đang mất kết nối: duyệt lại khi có mạng.";
export const FILL_RESULTS = "Nhập các ô kết quả rồi duyệt.";
export const HIDDEN_SUGGESTION = "Gợi ý là số tiền: cần quyền xem giá";

/** Each PO step's title and the words of its approval. */
export const PO_STEP_LABEL: Record<
  POStepKind,
  { title: string; approve: string; confirm: string }
> = {
  deposit_request: {
    title: "Bước 11: đề nghị đặt cọc (AI soạn)",
    approve: "Duyệt đề nghị đặt cọc",
    confirm:
      "Đề nghị thành hồ sơ đặt cọc của hồ sơ PO, hồ sơ sang chờ đặt cọc và Kế toán được báo.",
  },
  deposit_payment: {
    title: "Bước 11: xác nhận đã đặt cọc",
    approve: "Xác nhận đã cọc",
    confirm:
      "Khoản cọc với số tiền đã nhập được ghi vào hồ sơ và hồ sơ sang đã đặt cọc.",
  },
  final_payment_request: {
    title: "Bước 16: đề nghị thanh toán (AI soạn)",
    approve: "Duyệt đề nghị thanh toán",
    confirm:
      "Đề nghị thành hồ sơ thanh toán của hồ sơ PO, hồ sơ sang chờ thanh toán và Kế toán được báo.",
  },
  final_payment: {
    title: "Bước 16: xác nhận đã thanh toán",
    approve: "Xác nhận đã thanh toán",
    confirm:
      "Khoản thanh toán với số tiền đã nhập được ghi vào hồ sơ và hồ sơ sang đã thanh toán.",
  },
};
