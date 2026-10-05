import Link from "next/link";
import type { BriefEntry, BriefGroup, DailyBrief } from "@dw/contracts";
import { Card, CardContent, CardHeader, CardTitle } from "@dw/ui";
import { formatDateTime } from "../../lib/dates";
import { poCasesHref } from "../../lib/supply-chain/po-case-filter";
import { CASE_STATE_LABEL, CaseStateBadge } from "./case-state-badge";

/** Display names for the SLA milestones the platform ships. The set is data
 * (a tenant's SLA policy can name more), so an unknown one shows as its own
 * name rather than breaking the headline. */
const MILESTONE_LABEL: Record<string, string> = {
  deposit: "đặt cọc",
  port_arrival: "về cảng",
  payment: "thanh toán",
  warehouse_receipt: "nhập kho",
};

/** One sentence per group, composed here from the group's own fields. */
export function groupHeadline(group: BriefGroup): string {
  const n = group.total;
  switch (group.signal) {
    case "update_escalation_due":
      return `${n} case nhà cung cấp im lặng tới mức cần leo thang`;
    case "sla_breached": {
      const milestone = group.qualifier ?? "";
      return `${n} PO quá SLA ${MILESTONE_LABEL[milestone] ?? milestone}`.trim();
    }
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
        ? `${n} case ${CASE_STATE_LABEL[group.state].toLowerCase()}`
        : `${n} case chờ phía mình`;
    case "changed_recently":
      return `${n} case vừa đổi trạng thái trong 24 giờ qua`;
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
        ? formatDateTime(entry.transition.occurred_at)
        : "";
    case "case_blocked":
    case "manual_review":
    case "waiting_external":
    case "rework":
    case "waiting_on_us":
      return `${days} ngày ở trạng thái này`;
    default: {
      const unreachable: never = group.signal;
      return unreachable;
    }
  }
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

function GroupCard({ group }: { group: BriefGroup }) {
  const link = groupLink(group);
  const hidden = group.total - group.entries.length;
  return (
    <Card id={`brief-${group.key}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
        <CardTitle className="text-sm font-medium">
          {groupHeadline(group)}
        </CardTitle>
        {link && (
          <Link
            href={link.href}
            className="shrink-0 text-xs font-medium text-primary hover:underline"
          >
            {link.label}
          </Link>
        )}
      </CardHeader>
      <CardContent>
        <ul className="space-y-1.5">
          {group.entries.map((entry) => (
            <li
              key={entry.case.id}
              className="flex flex-wrap items-center gap-2 text-sm"
            >
              <Link
                href={`/supply-chain/po-cases/${entry.case.id}`}
                className="font-medium hover:underline"
              >
                {entry.case.po_reference}
              </Link>
              <span className="text-muted-foreground">
                {entry.case.supplier_name}
              </span>
              {entry.transition ? (
                <span className="flex items-center gap-1">
                  <CaseStateBadge state={entry.transition.from_state} />
                  <span aria-hidden="true">→</span>
                  <CaseStateBadge state={entry.transition.to_state} />
                </span>
              ) : null}
              <span className="text-xs text-muted-foreground">
                {entryDetail(group, entry)}
              </span>
            </li>
          ))}
        </ul>
        {hidden > 0 && (
          <p className="mt-2 text-xs text-muted-foreground">
            và {hidden} case khác.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * The daily management brief: what needs handling now, one card per
 * deterministic signal, in the order the server sends (the tenant's own brief
 * policy). Every sentence and link is composed here from structured fields;
 * nothing in the brief is text a model wrote.
 */
export function DailyBriefView({ brief }: { brief: DailyBrief }) {
  const tasks = brief.groups.filter((g) => g.signal !== "changed_recently");
  return (
    <div className="space-y-4">
      <p className="text-sm">
        <span className="font-medium">
          {brief.flagged_case_count} case cần xử lý
        </span>{" "}
        trong {brief.active_case_count} case đang chạy · lập lúc{" "}
        {formatDateTime(brief.generated_at)}
      </p>
      {!brief.approvals_visible && (
        // Not looked at is not "none pending": say which.
        <p className="text-xs text-muted-foreground">
          Bản tin không hiển thị phê duyệt đang chờ vì bạn không có quyền xem
          hộp phê duyệt.
        </p>
      )}
      {tasks.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Không có việc nào cần xử lý. Mốc SLA còn chờ xác nhận không được tính.
        </p>
      )}
      {brief.groups.map((group) => (
        <GroupCard key={group.key} group={group} />
      ))}
    </div>
  );
}
