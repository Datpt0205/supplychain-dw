"use client";

import { StatusTag, type StatusTone } from "@dw/ui";
import {
  caseDutySchema,
  type CaseDuty,
  type ProductAction,
  type ProductDevState,
  type SampleResult,
} from "@dw/contracts";

/**
 * The one table of Vietnamese labels for a product-development case's states,
 * copied from `packages/python/dw_supply_chain/CONTEXT.md` ("Trạng thái của Hồ
 * sơ phát triển sản phẩm"). Every screen that names a product state reads it
 * from here; when the glossary changes, both change in the same commit.
 */
export const PRODUCT_DEV_STATE_LABEL: Record<ProductDevState, string> = {
  proposed: "Đề xuất",
  sample_requested: "Đang lấy mẫu",
  sample_testing: "Đang test mẫu",
  revision_requested: "Chờ mẫu chỉnh sửa",
  pending_bod_review: "Chờ BGĐ duyệt",
  profile_in_progress: "Đang làm BM04",
  supplier_confirmation: "Chờ thống nhất với NCC",
  item_coding: "Đang tạo mã hàng",
  pending_signoff: "Chờ trình ký",
  ready_to_order: "Sẵn sàng đặt hàng",
  waiting_external: "Chờ bên ngoài",
  blocked: "Đang bị chặn",
  manual_review: "Cần xem xét thủ công",
  cancelled: "Đã hủy",
};

/**
 * Each state's step of the 17 (CONTEXT.md's "Bước, ghi chú" column; null for
 * an end or an interruption) and its tone. A step in progress is blue; the
 * waits for BGĐ and for the sign-off are their own tone, a decision pending;
 * signed off is done; only an interruption asks for attention. The label
 * always carries the meaning (ui-quality §7).
 */
export const PRODUCT_DEV_STATE_META: Record<
  ProductDevState,
  { step: number | null; tone: StatusTone }
> = {
  proposed: { step: 1, tone: "pri" },
  sample_requested: { step: 2, tone: "pri" },
  sample_testing: { step: 3, tone: "pri" },
  revision_requested: { step: 4, tone: "gold" },
  pending_bod_review: { step: 6, tone: "geek" },
  profile_in_progress: { step: 7, tone: "pri" },
  supplier_confirmation: { step: 8, tone: "pri" },
  item_coding: { step: 9, tone: "pri" },
  pending_signoff: { step: 9, tone: "geek" },
  ready_to_order: { step: 9, tone: "ok" },
  waiting_external: { step: null, tone: "gold" },
  blocked: { step: null, tone: "err" },
  manual_review: { step: null, tone: "warn" },
  cancelled: { step: null, tone: "gray" },
};

/** The case's state as the shared status tag. */
export function ProductDevStateTag({ state }: { state: ProductDevState }) {
  return (
    <StatusTag tone={PRODUCT_DEV_STATE_META[state].tone}>
      {PRODUCT_DEV_STATE_LABEL[state]}
    </StatusTag>
  );
}

/** What each step's button says: the outcome, in the glossary's words.
 * `bod_approve`, `bod_reject`, `signoff_approve` and `signoff_reject` are
 * never buttons: they are decided at `/approvals`; their labels name the step
 * in a case's history. */
export const PRODUCT_ACTION_LABEL: Record<ProductAction, string> = {
  propose: "Đề xuất sản phẩm",
  request_sample: "Yêu cầu mẫu",
  receive_sample: "Đã nhận mẫu",
  pass_sample: "Mẫu đạt",
  request_revision: "Yêu cầu chỉnh sửa",
  receive_revised_sample: "Đã nhận mẫu chỉnh sửa",
  reject_sample: "Hủy mẫu",
  complete_profile: "Hoàn tất BM04",
  confirm_with_supplier: "Đã thống nhất với NCC",
  issue_item_code: "Cấp mã hàng",
  add_sku: "Thêm SKU",
  remove_sku: "Bỏ SKU",
  submit_for_signoff: "Trình ký",
  wait_for_external: "Chờ bên ngoài",
  flag_blocked: "Báo bị chặn",
  flag_manual_review: "Cần xem xét thủ công",
  resume: "Tiếp tục",
  cancel: "Hủy hồ sơ",
  bod_approve: "BGĐ duyệt mẫu",
  bod_reject: "BGĐ không duyệt",
  signoff_approve: "Đã ký đủ",
  signoff_reject: "Không ký",
};

/** What a step still lacks (`unmet` of its option), in words. */
export const UNMET_LABEL: Record<string, string> = {
  item_code: "mã hàng chính thức",
  sku: "ít nhất một SKU",
};

/** Why a step the server offers is refused until something is done first. */
export function unmetLock(unmet: readonly string[]): string {
  const what = unmet.map((key) => UNMET_LABEL[key] ?? key).join(" và ");
  return `Cần ${what} trước.`;
}

/** A sample round's result (Vòng mẫu: Đạt, Cần chỉnh sửa, Hủy). */
export const SAMPLE_RESULT_LABEL: Record<SampleResult, string> = {
  passed: "Đạt",
  needs_revision: "Cần chỉnh sửa",
  rejected: "Hủy",
};

const SAMPLE_RESULT_TONE: Record<SampleResult, StatusTone> = {
  passed: "ok",
  needs_revision: "gold",
  rejected: "err",
};

/** A round's result; a round still open is being tested. */
export function SampleResultTag({ result }: { result: SampleResult | null }) {
  return result ? (
    <StatusTag tone={SAMPLE_RESULT_TONE[result]}>
      {SAMPLE_RESULT_LABEL[result]}
    </StatusTag>
  ) : (
    <StatusTag tone="pri">Đang test</StatusTag>
  );
}

/** Each duty in the glossary's words (`CONTEXT.md`, Duty). */
export const CASE_DUTY_LABEL: Record<CaseDuty, string> = {
  ordering: "Cung ứng",
  finance: "Kế toán",
  qc: "QC",
  logistics: "Logistics",
  warehouse: "Kho",
  exceptions: "xử lý ngoại lệ",
  rnd: "R&D",
  supply_lead: "TP Cung ứng",
};

// `supply_chain.duty.<duty>`: the scope a role grants for a duty. A second
// copy of the format, kept on purpose: the owner is
// `dw_supply_chain.application.handlers.duty_scope`, which sends the scope
// whole on a case's steps; these two read and write it for the one place a
// page starts from the policy instead (who may propose). A changed format
// locks the propose button for everyone (the server still decides), so the
// copy fails closed; it changes in the same commit as `duty_scope`.
const DUTY_SCOPE_PREFIX = "supply_chain.duty.";

export function dutyScope(duty: CaseDuty): string {
  return `${DUTY_SCOPE_PREFIX}${duty}`;
}

/** Why a step is locked for a viewer who lacks `requiredScope`, in words. */
export function dutyLock(requiredScope: string): string {
  const duty = caseDutySchema.safeParse(
    requiredScope.startsWith(DUTY_SCOPE_PREFIX)
      ? requiredScope.slice(DUTY_SCOPE_PREFIX.length)
      : null,
  ).data;
  const who = duty
    ? `nhiệm vụ ${CASE_DUTY_LABEL[duty]}`
    : `quyền ${requiredScope}`;
  return `Bước này cần ${who}; vai của bạn chưa có. Người có nhiệm vụ đó làm được bước này.`;
}
