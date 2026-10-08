"use client";

import { useCallback } from "react";
import Link from "next/link";
import {
  Card,
  Empty,
  Flex,
  Table,
  theme,
  Typography,
  type TableColumnsType,
} from "antd";
import type { PortfolioSummary } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../../components/load-error";
import { CaseQueryBar } from "../../../components/supply-chain/case-query-bar";
import {
  CASE_STATE_LABEL,
  CaseStateTag,
} from "../../../components/supply-chain/case-state-badge";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { apiClient } from "../../../lib/session";
import { poCasesHref } from "../../../lib/supply-chain/po-case-filter";
import { useCachedResource } from "../../../lib/use-cached-resource";

/**
 * The Control Tower's portfolio view: every active PO case counted by the
 * state it sits in and by supplier. Counts only — the same SLA-breach and
 * missing-update signals the Attention Queue flags on, computed by
 * `GET /control-tower/summary`, never a score. Which row is the real
 * bottleneck is the reader's call; rows arrive in the server's own order and
 * render as given.
 */
export default function ControlTowerPage() {
  const {
    data: summary,
    loading,
    error,
    reload,
  } = useCachedResource(
    "supply-chain:control-tower-summary",
    useCallback(() => apiClient().getPortfolioSummary(), []),
  );

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Control Tower")}
        title="Control Tower"
        subtitle="Mọi Hồ sơ PO đang chạy (bước 10–17), gom theo trạng thái và NCC: chỉ đếm tín hiệu xác định, không chấm điểm."
      />
      <Flex vertical gap="middle">
        <CaseQueryBar />
        {!summary ? (
          // A failed load is never shown as an empty portfolio: "nothing is
          // running" read off a 403 or an outage is an all-clear nobody gave.
          <>
            {error != null ? (
              <LoadError error={error} onRetry={reload} />
            ) : loading ? (
              <RegionState kind="loading" />
            ) : null}
          </>
        ) : summary.active_case_count === 0 ? (
          <Empty description="Chưa có Hồ sơ PO nào đang chạy. Hồ sơ đã hoàn tất hoặc đã hủy không được tính ở đây." />
        ) : (
          <>
            <Totals summary={summary} />
            <ByState rows={summary.by_state} />
            <BySupplier rows={summary.by_supplier} />
          </>
        )}
      </Flex>
    </div>
  );
}

function Totals({ summary }: { summary: PortfolioSummary }) {
  // Every tile counts CASES — a supplier with four quiet POs is four here,
  // one row in the supplier table below.
  const metrics = [
    {
      label: "Hồ sơ đang chạy",
      value: summary.active_case_count,
      alert: false,
    },
    {
      label: "Hồ sơ trễ SLA",
      value: summary.sla_breached_count,
      alert: true,
      // A milestone still pending business confirmation is never counted as
      // breached, so a 0 here can mean "not measured", not "on time".
      hint: "Chỉ tính mốc SLA đã được xác nhận",
    },
    {
      label: "Hồ sơ chậm cập nhật",
      value: summary.update_overdue_count,
      alert: true,
    },
  ];
  return (
    <Flex vertical gap="small">
      <div className="grid gap-3 sm:grid-cols-3">
        {metrics.map((metric) => (
          <Card key={metric.label} size="small">
            <Flex vertical>
              <Typography.Text type="secondary">{metric.label}</Typography.Text>
              <Count value={metric.value} alert={metric.alert} large />
              {metric.hint && (
                <Typography.Text type="secondary">
                  {metric.hint}
                </Typography.Text>
              )}
            </Flex>
          </Card>
        ))}
      </div>
      {(summary.sla_breached_count > 0 || summary.update_overdue_count > 0) && (
        <Typography.Text>
          Từng hồ sơ cụ thể nằm ở{" "}
          <Link href="/supply-chain/attention-queue">Cần chú ý</Link>.
        </Typography.Text>
      )}
    </Flex>
  );
}

/** A count that only draws the eye when it is a problem worth reading. */
function Count({
  value,
  alert,
  large,
}: {
  value: number;
  alert: boolean;
  large?: boolean;
}) {
  const { token } = theme.useToken();
  return (
    <Typography.Text
      strong={large}
      style={{
        color: alert && value > 0 ? token.colorErrorText : undefined,
        fontSize: large ? token.fontSizeHeading3 : undefined,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {value}
    </Typography.Text>
  );
}

type StateRow = PortfolioSummary["by_state"][number];
type SupplierRow = PortfolioSummary["by_supplier"][number];

function ByState({ rows }: { rows: StateRow[] }) {
  const columns: TableColumnsType<StateRow> = [
    {
      title: "Trạng thái",
      key: "state",
      render: (_: unknown, row) => (
        <Link
          // Every row here counts ACTIVE cases only, so each link asks for
          // exactly that set rather than relying on which states a row can
          // hold.
          href={poCasesHref({ state: row.state, activeOnly: true })}
          // Starts with the visible tag text (WCAG 2.5.3): a screen reader
          // hears which state, not only a count.
          aria-label={`${CASE_STATE_LABEL[row.state]}: xem ${row.case_count} hồ sơ`}
        >
          <CaseStateTag state={row.state} />
        </Link>
      ),
    },
    {
      title: "Số hồ sơ",
      dataIndex: "case_count",
      align: "right",
      render: (value: number) => <Count value={value} alert={false} />,
    },
    {
      title: "Trễ SLA",
      dataIndex: "sla_breached_count",
      align: "right",
      render: (value: number) => <Count value={value} alert />,
    },
    {
      title: "Chậm cập nhật",
      dataIndex: "update_overdue_count",
      align: "right",
      render: (value: number) => <Count value={value} alert />,
    },
    {
      title: "Lâu nhất ở bước này (ngày)",
      dataIndex: "oldest_in_state_days",
      align: "right",
      render: (value: number) => <Count value={value} alert={false} />,
    },
  ];
  return (
    <Card title="Theo trạng thái" size="small">
      <Table<StateRow>
        rowKey="state"
        size="small"
        pagination={false}
        sticky
        scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={rows}
        footer={() => "Hồ sơ đang nằm ở từng bước, theo thứ tự quy trình."}
      />
    </Card>
  );
}

function BySupplier({ rows }: { rows: SupplierRow[] }) {
  const columns: TableColumnsType<SupplierRow> = [
    {
      title: "NCC",
      key: "supplier",
      render: (_: unknown, row) => (
        <Link
          href={poCasesHref({
            supplierName: row.supplier_name,
            activeOnly: true,
          })}
        >
          {row.supplier_name}
        </Link>
      ),
    },
    {
      title: "Số hồ sơ",
      dataIndex: "case_count",
      align: "right",
      render: (value: number) => <Count value={value} alert={false} />,
    },
    {
      title: "Chậm cập nhật",
      dataIndex: "update_overdue_count",
      align: "right",
      render: (value: number) => <Count value={value} alert />,
    },
    {
      title: "Cần leo thang",
      dataIndex: "escalation_due_count",
      align: "right",
      render: (value: number) => <Count value={value} alert />,
    },
    {
      title: "Trễ SLA",
      dataIndex: "sla_breached_count",
      align: "right",
      render: (value: number) => <Count value={value} alert />,
    },
    {
      title: "Im lặng lâu nhất (ngày)",
      dataIndex: "longest_silence_days",
      align: "right",
      render: (value: number) => <Count value={value} alert={false} />,
    },
  ];
  return (
    <Card title="Theo NCC" size="small">
      <Table<SupplierRow>
        rowKey="supplier_name"
        size="small"
        pagination={false}
        sticky
        scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={rows}
        footer={() => "NCC có nhiều hồ sơ chậm cập nhật nhất đứng đầu."}
      />
    </Card>
  );
}
