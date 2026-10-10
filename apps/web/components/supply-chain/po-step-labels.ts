/** Words of the PO step card (tickets ai-automation/15-18), shared with its tests. */
import type { POStepKind } from "@dw/contracts";

export const OFFLINE_STEP = "Đang mất kết nối: duyệt lại khi có mạng.";
export const FILL_RESULTS = "Nhập các ô kết quả rồi duyệt.";
export const HIDDEN_SUGGESTION = "Gợi ý là số tiền: cần quyền xem giá";

/** The words of a choice a person makes at a step (QC's verdict). */
export const RESULT_OPTION_LABEL: Record<string, string> = {
  pass: "Đạt",
  fail: "Không đạt",
};

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
  production: {
    title: "Bước 13: sản xuất (AI đọc lịch của NCC)",
    approve: "Chuyển sang QC",
    confirm:
      "Ngày ETD đã nhập được ghi vào hồ sơ và hồ sơ sang kiểm hàng (QC).",
  },
  qc: {
    title: "Bước 14: kết quả QC (AI gợi ý theo số lỗi)",
    approve: "Ghi kết quả QC",
    confirm:
      "Đạt: hồ sơ sang vận chuyển, số container được ghi. Không đạt: phiếu yêu cầu sửa hàng thành chứng từ của hồ sơ và hồ sơ sang sửa hàng.",
  },
  arrival: {
    title: "Bước 15: hàng về cảng (AI kiểm bộ chứng từ)",
    approve: "Xác nhận hàng đến cảng",
    confirm: "Ngày ETA đã nhập được ghi vào hồ sơ và hồ sơ sang hàng đến cảng.",
  },
  warehouse: {
    title: "Bước 17: nhập kho (AI soạn phiếu, Kho đếm)",
    approve: "Ghi số đếm và nhập kho",
    confirm:
      "Số đếm từng dòng được ghi, phiếu nhập kho thành chứng từ của hồ sơ và hồ sơ hoàn tất. Dòng nào khác số NCC giao thì AI soạn biên bản chênh lệch và thư khiếu nại.",
  },
};
