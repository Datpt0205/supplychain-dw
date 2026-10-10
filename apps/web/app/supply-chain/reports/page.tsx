"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from "antd";
import type {
  AiAcceptance,
  ReportFigure,
  SupplierScore,
  WeeklySummary,
} from "@dw/contracts";
import { PageHeader } from "@dw/ui";
import { LoadError } from "../../../components/load-error";
import { DOC_TYPE_LABEL } from "../../../components/supply-chain/case-documents-card";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import {
  ESTIMATE_NOTE,
  SUMMARY_NOTE,
} from "../../../components/supply-chain/report-labels";
import { formatDate } from "../../../lib/dates";
import { errorMessage } from "../../../lib/error-message";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

function WeeklyCard() {
  const report = useCachedResource(
    "supply-chain:reports:weekly",
    useCallback(() => apiClient().getWeeklyReport(), []),
  );
  const [summary, setSummary] = useState<WeeklySummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const data = report.data;

  const summarize = async () => {
    setBusy(true);
    setError(null);
    try {
      setSummary(await apiClient().summarizeWeeklyReport());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const columns: TableColumnsType<ReportFigure> = [
    { title: "Số liệu", key: "label", render: (_, f) => f.label },
    {
      title: "Số",
      key: "value",
      align: "right",
      render: (_, f) => f.value,
    },
    {
      title: "Hồ sơ được đếm",
      key: "cases",
      render: (_, f) => f.cases.join(", ") || null,
    },
  ];

  return (
    <Card
      title={
        data
          ? `Báo cáo tuần ${formatDate(data.week_start)} – ${formatDate(data.week_end)}`
          : "Báo cáo tuần"
      }
    >
      <Flex vertical gap="middle">
        {report.error != null && !data ? (
          <LoadError error={report.error} onRetry={report.reload} compact />
        ) : (
          <Table<ReportFigure>
            rowKey="key"
            size="small"
            pagination={false}
            loading={report.loading}
            columns={columns}
            dataSource={data?.figures ?? []}
            scroll={{ x: "max-content" }}
          />
        )}
        <Flex vertical gap="small">
          <Typography.Text>{SUMMARY_NOTE}</Typography.Text>
          <Button onClick={() => void summarize()} loading={busy}>
            AI tóm tắt tuần
          </Button>
          {error && <Alert type="error" showIcon title={error} />}
          {summary && (
            <Flex vertical gap="small" role="status">
              {summary.sentences.length === 0 ? (
                <Typography.Text>
                  AI không có câu nào khớp với số đã đếm; xem bảng số ở trên.
                </Typography.Text>
              ) : (
                <>
                  <Tag>AI viết, đã kiểm với số</Tag>
                  {summary.sentences.map((sentence, index) => (
                    <Flex key={index} vertical gap={2}>
                      <Typography.Text>{sentence.text}</Typography.Text>
                      <Typography.Text type="secondary">
                        Dựa trên:{" "}
                        {sentence.cites.map((c) => c.label).join(", ")}
                      </Typography.Text>
                    </Flex>
                  ))}
                </>
              )}
            </Flex>
          )}
        </Flex>
      </Flex>
    </Card>
  );
}

function rate(value: number | null): string | null {
  return value === null ? null : `${Math.round(value * 100)}%`;
}

function SupplierCard() {
  const scores = useCachedResource(
    "supply-chain:reports:suppliers",
    useCallback(() => apiClient().getSupplierScorecard(), []),
  );
  const columns: TableColumnsType<SupplierScore> = [
    { title: "NCC", key: "supplier", render: (_, s) => s.supplier },
    { title: "PO", key: "po", align: "right", render: (_, s) => s.po_total },
    {
      title: "Đang mở",
      key: "open",
      align: "right",
      render: (_, s) => s.po_open,
    },
    {
      title: "Hoàn tất",
      key: "done",
      align: "right",
      render: (_, s) => s.po_completed,
    },
    {
      title: "QC trả làm lại",
      key: "rework",
      align: "right",
      render: (_, s) => s.qc_reworks,
    },
    {
      title: "Vòng mẫu đạt / sửa / hủy",
      key: "rounds",
      render: (_, s) =>
        `${s.rounds_passed} / ${s.rounds_revised} / ${s.rounds_rejected}`,
    },
    {
      title: "Tỷ lệ mẫu đạt",
      key: "rate",
      align: "right",
      render: (_, s) => rate(s.sample_pass_rate) ?? "chưa có vòng đóng",
    },
    {
      title: "Dòng kho đếm lệch",
      key: "lines",
      align: "right",
      render: (_, s) => s.lines_differing,
    },
  ];
  return (
    <Card title="Điểm NCC (đếm từ hồ sơ)">
      {scores.error != null && !scores.data ? (
        <LoadError error={scores.error} onRetry={scores.reload} compact />
      ) : (
        <Table<SupplierScore>
          rowKey="supplier"
          size="small"
          pagination={false}
          loading={scores.loading}
          columns={columns}
          dataSource={scores.data ?? []}
          scroll={{ x: "max-content" }}
          locale={{
            emptyText: scores.loading ? (
              " "
            ) : (
              <Empty description="Chưa có NCC nào có hồ sơ." />
            ),
          }}
        />
      )}
    </Card>
  );
}

type AcceptanceRow = AiAcceptance["rows"][number];

function AcceptanceCard() {
  const report = useCachedResource(
    "supply-chain:reports:ai-acceptance",
    useCallback(() => apiClient().getAiAcceptance(), []),
  );
  const columns: TableColumnsType<AcceptanceRow> = [
    {
      title: "Chứng từ AI soạn",
      key: "type",
      render: (_, r) =>
        DOC_TYPE_LABEL[r.doc_type as keyof typeof DOC_TYPE_LABEL] ?? r.doc_type,
    },
    {
      title: "Đã soạn",
      key: "drafted",
      align: "right",
      render: (_, r) => r.drafted,
    },
    {
      title: "Duyệt nguyên",
      key: "as_is",
      align: "right",
      render: (_, r) => r.as_is,
    },
    {
      title: "Sửa rồi duyệt",
      key: "edited",
      align: "right",
      render: (_, r) => r.edited,
    },
    {
      title: "Từ chối",
      key: "rejected",
      align: "right",
      render: (_, r) => r.rejected,
    },
    { title: "Còn mở", key: "open", align: "right", render: (_, r) => r.open },
    {
      title: "Phút tiết kiệm (ước tính)",
      key: "minutes",
      align: "right",
      render: (_, r) => r.minutes_saved,
    },
  ];
  const data = report.data;
  return (
    <Card title="AI được duyệt bao nhiêu">
      <Flex vertical gap="small">
        <Typography.Text>{ESTIMATE_NOTE}</Typography.Text>
        {report.error != null && !data ? (
          <LoadError error={report.error} onRetry={report.reload} compact />
        ) : (
          <Table<AcceptanceRow>
            rowKey="doc_type"
            size="small"
            pagination={false}
            loading={report.loading}
            columns={columns}
            dataSource={data?.rows ?? []}
            scroll={{ x: "max-content" }}
            locale={{
              emptyText: report.loading ? (
                " "
              ) : (
                <Empty description="Chưa có bản nháp nào AI soạn trong 90 ngày." />
              ),
            }}
            footer={
              data
                ? () =>
                    `Từ ${formatDate(data.since)}; số phút theo chính sách ${data.policy_version}.`
                : undefined
            }
          />
        )}
      </Flex>
    </Card>
  );
}

/**
 * Báo cáo (ticket ai-automation/20): báo cáo tuần cho BGĐ, điểm NCC và AI được
 * duyệt bao nhiêu. Mọi con số do hệ thống đếm từ hồ sơ; câu tóm tắt của AI chỉ
 * được giữ khi số của nó đúng là số đã đếm.
 */
export default function ReportsPage() {
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Báo cáo")}
        title="Báo cáo"
        subtitle="Báo cáo tuần cho BGĐ, điểm NCC và tỷ lệ bản nháp AI được duyệt. Số do hệ thống đếm từ hồ sơ, không do AI."
      />
      <Flex vertical gap="middle">
        <WeeklyCard />
        <SupplierCard />
        <AcceptanceCard />
      </Flex>
    </div>
  );
}
