"use client";

import { useCallback } from "react";
import Link from "next/link";
import { TowerControl } from "lucide-react";
import type { PortfolioSummary } from "@dw/contracts";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  cn,
} from "@dw/ui";
import { EmptyState } from "../../../components/empty-state";
import { PageHeading } from "../../../components/page-heading";
import { CaseQueryBar } from "../../../components/supply-chain/case-query-bar";
import {
  CASE_STATE_LABEL,
  CaseStateBadge,
} from "../../../components/supply-chain/case-state-badge";
import { apiClient } from "../../../lib/session";
import { poCasesHref } from "../../../lib/supply-chain/po-case-filter";
import { useCachedResource } from "../../../lib/use-cached-resource";

/**
 * The Control Tower's portfolio view: every
 * active case counted by the state it sits in and by supplier. Counts only —
 * the same SLA-breach and missing-update signals the Attention Queue flags
 * on, computed by `GET /control-tower/summary`, never a score. Which row is
 * the real bottleneck is the reader's call; rows arrive in the server's own
 * order and render as given.
 */
export default function ControlTowerPage() {
  const {
    data: summary,
    loading,
    error,
  } = useCachedResource(
    "supply-chain:control-tower-summary",
    useCallback(() => apiClient().getPortfolioSummary(), []),
  );

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeading
        icon={TowerControl}
        title="Control Tower"
        description="Toàn bộ case đang chạy, gom theo trạng thái và nhà cung cấp — chỉ đếm tín hiệu xác định, không chấm điểm."
      />
      <CaseQueryBar />
      {loading ? (
        <Skeleton className="h-96 w-full" />
      ) : error != null || !summary ? (
        // A failed load is never shown as an empty portfolio: "nothing is
        // running" read off a 403 or an outage is an all-clear nobody gave.
        <p className="text-sm text-destructive">
          Không tải được dữ liệu Control Tower:{" "}
          {error instanceof Error ? error.message : "lỗi không xác định"}
        </p>
      ) : summary.active_case_count === 0 ? (
        <EmptyState
          icon={TowerControl}
          title="Chưa có case nào đang chạy"
          description="Case đã hoàn tất hoặc đã hủy không được tính ở đây."
        />
      ) : (
        <>
          <Totals summary={summary} />
          <ByState rows={summary.by_state} />
          <BySupplier rows={summary.by_supplier} />
        </>
      )}
    </div>
  );
}

function Totals({ summary }: { summary: PortfolioSummary }) {
  // Every tile counts CASES — a supplier with four quiet POs is four here,
  // one row in the supplier table below.
  const metrics = [
    { label: "Case đang chạy", value: summary.active_case_count, alert: false },
    {
      label: "Case trễ SLA",
      value: summary.sla_breached_count,
      alert: true,
      // A milestone still pending business confirmation is never counted as
      // breached, so a 0 here can mean "not measured", not "on time".
      hint: "Chỉ tính mốc SLA đã được xác nhận",
    },
    {
      label: "Case chậm cập nhật",
      value: summary.update_overdue_count,
      alert: true,
    },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {metrics.map((metric) => (
        <Card key={metric.label}>
          <CardHeader className="pb-2">
            <CardDescription>{metric.label}</CardDescription>
            <CardTitle className="text-3xl">
              <Count value={metric.value} alert={metric.alert} />
            </CardTitle>
            {metric.hint && (
              <p className="text-xs text-muted-foreground">{metric.hint}</p>
            )}
          </CardHeader>
        </Card>
      ))}
      {(summary.sla_breached_count > 0 || summary.update_overdue_count > 0) && (
        <p className="text-sm text-muted-foreground sm:col-span-3">
          Từng case cụ thể nằm ở{" "}
          <Link
            href="/supply-chain/attention-queue"
            className="font-medium text-foreground hover:underline"
          >
            Cần chú ý
          </Link>
          .
        </p>
      )}
    </div>
  );
}

/** A count that only draws the eye when it is a problem worth reading. */
function Count({ value, alert }: { value: number; alert: boolean }) {
  return (
    <span className={cn(alert && value > 0 && "text-destructive")}>
      {value}
    </span>
  );
}

function ByState({ rows }: { rows: PortfolioSummary["by_state"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Theo trạng thái</CardTitle>
        <CardDescription>
          Case đang nằm ở từng bước, theo thứ tự quy trình.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Trạng thái</TableHead>
              <TableHead className="text-right">Số case</TableHead>
              <TableHead className="text-right">Trễ SLA</TableHead>
              <TableHead className="text-right">Chậm cập nhật</TableHead>
              <TableHead className="text-right">
                Lâu nhất ở bước này (ngày)
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.state}>
                <TableCell>
                  <Link
                    // Every row here counts ACTIVE cases only, so each
                    // link asks for exactly that set rather than relying on
                    // which states a row can hold.
                    href={poCasesHref({ state: row.state, activeOnly: true })}
                    // Starts with the visible badge text (WCAG 2.5.3): a
                    // screen reader hears which state, not only a count.
                    aria-label={`${CASE_STATE_LABEL[row.state]}: xem ${row.case_count} case`}
                  >
                    <CaseStateBadge state={row.state} />
                  </Link>
                </TableCell>
                <TableCell className="text-right">{row.case_count}</TableCell>
                <TableCell className="text-right">
                  <Count value={row.sla_breached_count} alert />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.update_overdue_count} alert />
                </TableCell>
                <TableCell className="text-right">
                  {row.oldest_in_state_days}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function BySupplier({ rows }: { rows: PortfolioSummary["by_supplier"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Theo nhà cung cấp</CardTitle>
        <CardDescription>
          Nhà cung cấp có nhiều case chậm cập nhật nhất đứng đầu.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nhà cung cấp</TableHead>
              <TableHead className="text-right">Số case</TableHead>
              <TableHead className="text-right">Chậm cập nhật</TableHead>
              <TableHead className="text-right">Cần leo thang</TableHead>
              <TableHead className="text-right">Trễ SLA</TableHead>
              <TableHead className="text-right">
                Im lặng lâu nhất (ngày)
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={row.supplier_name}>
                <TableCell className="font-medium">
                  <Link
                    href={poCasesHref({
                      supplierName: row.supplier_name,
                      activeOnly: true,
                    })}
                    className="hover:underline"
                  >
                    {row.supplier_name}
                  </Link>
                </TableCell>
                <TableCell className="text-right">{row.case_count}</TableCell>
                <TableCell className="text-right">
                  <Count value={row.update_overdue_count} alert />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.escalation_due_count} alert />
                </TableCell>
                <TableCell className="text-right">
                  <Count value={row.sla_breached_count} alert />
                </TableCell>
                <TableCell className="text-right">
                  {row.longest_silence_days}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
