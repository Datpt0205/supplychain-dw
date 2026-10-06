"use client";

import { Tag } from "antd";
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
  waiting_external: "Chờ bên ngoài",
  blocked: "Đang bị chặn",
  manual_review: "Cần xem xét thủ công",
  cancelled: "Đã hủy",
};

/**
 * Each state's antd status preset: a theme token, never a colour of its own.
 * Text always carries the meaning; the colour only groups (ui-quality §7).
 */
const STATE_STATUS: Record<
  ProductDevState,
  "default" | "processing" | "success" | "warning" | "error"
> = {
  proposed: "default",
  sample_requested: "processing",
  sample_testing: "processing",
  revision_requested: "warning",
  pending_bod_review: "success",
  profile_in_progress: "processing",
  waiting_external: "warning",
  blocked: "error",
  manual_review: "warning",
  cancelled: "default",
};

/** The case's state as a tag. Built here with antd's `Tag` until `@dw/ui`
 * ships its status tag (antd-shell 07/08); then this swaps for that. */
export function ProductDevStateTag({ state }: { state: ProductDevState }) {
  return (
    <Tag color={STATE_STATUS[state]} bordered={false}>
      {PRODUCT_DEV_STATE_LABEL[state]}
    </Tag>
  );
}

/** What each step's button says: the outcome, in the glossary's words.
 * `bod_approve` and `bod_reject` are never buttons: BGĐ decides at
 * `/approvals`; their labels name the step in a case's history. */
export const PRODUCT_ACTION_LABEL: Record<ProductAction, string> = {
  propose: "Đề xuất sản phẩm",
  request_sample: "Yêu cầu mẫu",
  receive_sample: "Đã nhận mẫu",
  pass_sample: "Mẫu đạt",
  request_revision: "Yêu cầu chỉnh sửa",
  receive_revised_sample: "Đã nhận mẫu chỉnh sửa",
  reject_sample: "Hủy mẫu",
  wait_for_external: "Chờ bên ngoài",
  flag_blocked: "Báo bị chặn",
  flag_manual_review: "Cần xem xét thủ công",
  resume: "Tiếp tục",
  cancel: "Hủy hồ sơ",
  bod_approve: "BGĐ duyệt mẫu",
  bod_reject: "BGĐ không duyệt",
};

/** A sample round's result (Vòng mẫu: Đạt, Cần chỉnh sửa, Hủy). */
export const SAMPLE_RESULT_LABEL: Record<SampleResult, string> = {
  passed: "Đạt",
  needs_revision: "Cần chỉnh sửa",
  rejected: "Hủy",
};

/** Each duty in the glossary's words (`CONTEXT.md`, Duty). */
export const CASE_DUTY_LABEL: Record<CaseDuty, string> = {
  ordering: "Cung ứng",
  finance: "Kế toán",
  qc: "QC",
  logistics: "Logistics",
  warehouse: "Kho",
  exceptions: "xử lý ngoại lệ",
  rnd: "R&D",
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
