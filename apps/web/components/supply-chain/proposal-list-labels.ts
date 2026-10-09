import type { ProposalListDetail } from "@dw/api-client";

/** Words of the proposal list pages (ticket ai-automation/08), one table each
 * (ui-quality §7). A page file exports only its page, so they live here. */
export const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NEEDS_VALUES = "Cần mã đề xuất, tên sản phẩm và nhóm sản phẩm.";
export const PRIORITY_LABEL: Record<string, string> = {
  high: "Ưu tiên cao",
  normal: "Bình thường",
  low: "Ưu tiên thấp",
};
export const STATUS_TEXT: Record<
  NonNullable<ProposalListDetail["status"]>,
  string
> = {
  extracted: "AI đã đọc",
  unreadable: "Máy không đọc được file này; hãy đề xuất tay.",
  refused: "AI không đọc được danh sách theo mẫu; hãy đề xuất tay.",
  failed: "Đọc file bị lỗi; hãy tải lại hoặc đề xuất tay.",
};
