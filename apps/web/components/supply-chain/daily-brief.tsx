import Link from "next/link";
import { Card, Empty, Flex, Typography } from "antd";
import type {
  BriefEntry,
  BriefGroup,
  DailyBrief,
  ProductBriefEntry,
  SampleResult,
  WorkspaceMember,
} from "@dw/contracts";
import { formatDateTimeFull } from "../../lib/dates";
import { memberName } from "../../lib/directory";
import { poCasesHref } from "../../lib/supply-chain/po-case-filter";
import { productCasesHref } from "../../lib/supply-chain/product-case-filter";
import { CASE_STATE_LABEL, CaseStateTag } from "./case-state-badge";
import { PoReferenceText } from "./po-reference";
import { SAMPLE_RESULT_LABEL } from "./product-case-labels";
import { milestoneLabel } from "./sla-status-badge";

/** "Chờ tạo PO" inside a sentence: only the first letter lowered, so "PO"
 * stays an acronym. */
function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

/** One sentence per group, composed here from the group's own fields. */
export function groupHeadline(group: BriefGroup): string {
  const n = group.total;
  switch (group.signal) {
    case "update_escalation_due":
      return `${n} case nhà cung cấp im lặng tới mức cần leo thang`;
    case "sla_breached":
      return `${n} PO quá SLA ${milestoneLabel(group.qualifier)}`.trim();
    case "case_blocked":
      return `${n} case đang bị chặn`;
    case "approval_pending":
      return `${n} phê duyệt đang chờ quyết định`;
    case "manual_review":
      return `${n} case cần xem xét thủ công`;
    case "supplier_reported_delay":
      return `${n} case nhà cung cấp báo trễ`;
    case "update_reminder_due":
      return `${n} case cần nhắc nhà cung cấp cập nhật`;
    case "waiting_external":
      return `${n} case chờ bên ngoài`;
    case "rework":
      return `${n} case đang làm lại`;
    case "waiting_on_us":
      return group.state
        ? `${n} case ${lowerFirst(CASE_STATE_LABEL[group.state])}`
        : `${n} case chờ phía mình`;
    case "changed_recently":
      return `${n} case vừa đổi trạng thái trong 24 giờ qua`;
    case "product_sla_breached":
      return `${n} hồ sơ phát triển quá hạn ${milestoneLabel(group.qualifier)}`.trim();
    case "product_awaiting_bod":
      return `${n} hồ sơ phát triển chờ BGĐ duyệt`;
    case "product_awaiting_signoff":
      return `${n} hồ sơ phát triển chờ trình ký`;
    case "sample_evaluated_today": {
      const result = group.qualifier as SampleResult | null;
      return result && result in SAMPLE_RESULT_LABEL
        ? `${n} mẫu đánh giá hôm nay: ${SAMPLE_RESULT_LABEL[result]}`
        : `${n} mẫu đánh giá hôm nay`;
    }
    default: {
      const unreachable: never = group.signal;
      return unreachable;
    }
  }
}

/** The figure that put this case in this group, in words. */
function entryDetail(group: BriefGroup, entry: BriefEntry): string {
  const days = entry.days ?? 0;
  switch (group.signal) {
    case "update_escalation_due":
    case "update_reminder_due":
      return `im lặng ${days} ngày`;
    case "sla_breached":
      return entry.limit_days != null
        ? `${days} ngày, hạn ${entry.limit_days} ngày`
        : `${days} ngày`;
    case "approval_pending":
      return `chờ duyệt ${days} ngày · ${entry.approval_action ?? ""}`;
    case "supplier_reported_delay":
      return `báo trễ ${days} ngày`;
    case "changed_recently":
      return entry.transition
        ? formatDateTimeFull(entry.transition.occurred_at)
        : "";
    case "case_blocked":
    case "manual_review":
    case "waiting_external":
    case "rework":
    case "waiting_on_us":
      return `${days} ngày ở trạng thái này`;
    // Stage-1 groups carry product entries, rendered by `productDetail`.
    case "product_sla_breached":
    case "product_awaiting_bod":
    case "product_awaiting_signoff":
    case "sample_evaluated_today":
      return "";
    default: {
      const unreachable: never = group.signal;
      return unreachable;
    }
  }
}

/** The figure that put this product case in this stage-1 group, in words. */
function productDetail(group: BriefGroup, entry: ProductBriefEntry): string {
  const days = entry.days ?? 0;
  if (group.signal === "sample_evaluated_today")
    return entry.round_no !== null ? `vòng mẫu ${entry.round_no}` : "";
  if (group.signal === "product_sla_breached")
    return entry.limit_days != null
      ? `${days} ngày, hạn ${entry.limit_days} ngày`
      : `${days} ngày`;
  return `chờ ${days} ngày`;
}

/** Where the whole group can be read: the case list filtered to the group's
 * own state when a state defines it, otherwise the page that lists that
 * signal. A reported delay and a recent change have no such page. */
function groupLink(group: BriefGroup): { href: string; label: string } | null {
  if (group.state) {
    return {
      href: poCasesHref({ state: group.state, activeOnly: true }),
      label: "Mở danh sách",
    };
  }
  if (group.product_state) {
    return {
      href: productCasesHref({ state: group.product_state }),
      label: "Mở danh sách",
    };
  }
  switch (group.signal) {
    case "sla_breached":
    case "update_escalation_due":
    case "update_reminder_due":
      return { href: "/supply-chain/attention-queue", label: "Mở Cần chú ý" };
    case "approval_pending":
      return { href: "/approvals", label: "Mở hộp phê duyệt" };
    default:
      return null;
  }
}

function GroupCard({
  group,
  members,
}: {
  group: BriefGroup;
  members: WorkspaceMember[];
}) {
  const link = groupLink(group);
  const hidden =
    group.total - group.entries.length - group.product_entries.length;
  // Samples evaluated today read by PIC (the server sorts them so); a group
  // of cases waiting reads by how long.
  const byPic = group.signal === "sample_evaluated_today";
  return (
    <Card
      id={`brief-${group.key}`}
      size="small"
      title={groupHeadline(group)}
      extra={link ? <Link href={link.href}>{link.label}</Link> : null}
    >
      <Flex vertical gap="small" role="list">
        {group.entries.map((entry) => (
          <Flex
            key={entry.case.id}
            role="listitem"
            wrap
            gap="small"
            align="center"
          >
            <Link href={`/supply-chain/po-cases/${entry.case.id}`}>
              <PoReferenceText reference={entry.case.po_reference} />
            </Link>
            <Typography.Text type="secondary">
              {entry.case.supplier_name}
            </Typography.Text>
            {entry.transition ? (
              <Flex gap="small" align="center">
                <CaseStateTag state={entry.transition.from_state} />
                <span aria-hidden="true">→</span>
                <CaseStateTag state={entry.transition.to_state} />
              </Flex>
            ) : null}
            <Typography.Text>{entryDetail(group, entry)}</Typography.Text>
          </Flex>
        ))}
        {group.product_entries.map((entry) => (
          <Flex
            key={`${entry.case.id}-${entry.round_no ?? ""}`}
            role="listitem"
            wrap
            gap="small"
            align="center"
          >
            <Link href={`/supply-chain/product-cases/${entry.case.id}`}>
              {entry.case.proposal_code}
            </Link>
            <Typography.Text type="secondary" ellipsis={{ tooltip: true }}>
              {entry.case.product_name}
            </Typography.Text>
            {byPic && (
              <Typography.Text>
                PIC: {memberName(members, entry.case.pic_user_id)}
              </Typography.Text>
            )}
            <Typography.Text>{productDetail(group, entry)}</Typography.Text>
          </Flex>
        ))}
        {hidden > 0 && (
          <Typography.Text type="secondary">
            và {hidden} {group.product_entries.length > 0 ? "hồ sơ" : "case"}{" "}
            khác.
          </Typography.Text>
        )}
      </Flex>
    </Card>
  );
}

/**
 * The daily management brief: what needs handling now, one card per
 * deterministic signal, in the order the server sends (the tenant's own brief
 * policy). Every sentence and link is composed here from structured fields;
 * nothing in the brief is text a model wrote.
 */
export function DailyBriefView({
  brief,
  members = [],
}: {
  brief: DailyBrief;
  /** The workspace roster, for PIC names; ids show until it arrives. */
  members?: WorkspaceMember[];
}) {
  const tasks = brief.groups.filter(
    (g) =>
      g.signal !== "changed_recently" && g.signal !== "sample_evaluated_today",
  );
  return (
    <Flex vertical gap="middle">
      <Typography.Text>
        <Typography.Text strong>
          {brief.flagged_case_count} case cần xử lý
        </Typography.Text>{" "}
        trong {brief.active_case_count} case đang chạy · lập lúc{" "}
        {formatDateTimeFull(brief.generated_at)}
      </Typography.Text>
      {brief.product_cases_visible ? (
        <Typography.Text>
          <Typography.Text strong>
            {brief.flagged_product_case_count} hồ sơ phát triển cần xử lý
          </Typography.Text>{" "}
          trong {brief.active_product_case_count} hồ sơ đang chạy
        </Typography.Text>
      ) : (
        // Not looked at is not "nothing in stage 1": say which.
        <Typography.Text>
          Bản tin không hiển thị hồ sơ phát triển sản phẩm vì bạn không có quyền
          xem hồ sơ phát triển.
        </Typography.Text>
      )}
      {!brief.approvals_visible && (
        // Not looked at is not "none pending": say which.
        <Typography.Text>
          Bản tin không hiển thị phê duyệt đang chờ vì bạn không có quyền xem
          hộp phê duyệt.
        </Typography.Text>
      )}
      {tasks.length === 0 && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="Không có việc nào cần xử lý. Mốc SLA còn chờ xác nhận không được tính."
        />
      )}
      {brief.groups.map((group) => (
        <GroupCard key={group.key} group={group} members={members} />
      ))}
    </Flex>
  );
}
