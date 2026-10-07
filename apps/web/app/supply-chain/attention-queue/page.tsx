"use client";

import { useCallback } from "react";
import Link from "next/link";
import { Empty, Flex, Table, Typography, type TableColumnsType } from "antd";
import type { AttentionItem } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { CaseStateTag } from "../../../components/supply-chain/case-state-badge";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { MissingUpdateTag } from "../../../components/supply-chain/missing-update-badge";
import { PoReferenceText } from "../../../components/supply-chain/po-reference";
import {
  SlaStatusTag,
  milestoneLabel,
} from "../../../components/supply-chain/sla-status-badge";
import { regionFailure } from "../../../lib/error-message";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

/**
 * The Attention Queue: every PO case with a deterministic signal — SLA
 * breach, a supplier gone quiet — gathered in one place. Nothing here is
 * invented or ranked by a model; `GET /attention-queue` computes both
 * signals the same way the Case Workspace's own health cards do, just for
 * every active case in the tenant instead of one. Rows arrive in the
 * server's order and render as given.
 */
export default function AttentionQueuePage() {
  const { data, loading, error, reload } = useCachedResource(
    "supply-chain:attention-queue",
    useCallback(() => apiClient().listAttentionQueue(), []),
  );

  const columns: TableColumnsType<AttentionItem> = [
    {
      title: "Hồ sơ PO",
      key: "case",
      render: (_: unknown, item) => (
        <Flex vertical>
          <Link href={`/supply-chain/po-cases/${item.case.id}`}>
            <PoReferenceText reference={item.case.po_reference} />
          </Link>
          <Typography.Text type="secondary">
            {item.case.supplier_name}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: "Trạng thái",
      key: "state",
      render: (_: unknown, item) => <CaseStateTag state={item.case.state} />,
    },
    {
      title: "SLA",
      key: "sla",
      render: (_: unknown, item) =>
        item.sla ? (
          <Flex vertical gap={2} align="start">
            <SlaStatusTag status={item.sla.status} />
            {item.sla.milestone && (
              <Typography.Text type="secondary">
                Mốc {milestoneLabel(item.sla.milestone)} · {item.sla.age_days}{" "}
                ngày
                {item.sla.threshold_days !== null &&
                  ` / hạn ${item.sla.threshold_days} ngày`}
              </Typography.Text>
            )}
          </Flex>
        ) : (
          <Typography.Text type="secondary">Không có tín hiệu</Typography.Text>
        ),
    },
    {
      title: "Cập nhật của NCC",
      key: "missing",
      render: (_: unknown, item) =>
        item.missing_update ? (
          <Flex vertical gap={2} align="start">
            <MissingUpdateTag status={item.missing_update.status} />
            <Typography.Text type="secondary">
              Im lặng {item.missing_update.age_days} ngày
            </Typography.Text>
          </Flex>
        ) : (
          <Typography.Text type="secondary">Không có tín hiệu</Typography.Text>
        ),
    },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Cần chú ý")}
        title="Cần chú ý"
        description={
          data
            ? `${data.length} Hồ sơ PO trễ SLA hoặc NCC im lặng quá lâu. Tính bằng quy tắc, không suy diễn.`
            : "Hồ sơ PO trễ SLA hoặc NCC im lặng quá lâu. Tính bằng quy tắc, không suy diễn."
        }
      />
      {/* Never "every case is on track" read off a failed request: that is
          the one claim this page exists to make, and a 403 did not make it. */}
      {error != null && !data ? (
        <RegionState
          failure={regionFailure(error)}
          what="danh sách cần chú ý"
          onRetry={reload}
        />
      ) : (
        <Table<AttentionItem>
          rowKey={(item) => item.case.id}
          size="middle"
          pagination={false}
          sticky
          scroll={{ x: "max-content" }}
          loading={loading}
          columns={columns}
          dataSource={data ?? []}
          locale={{
            emptyText: loading ? (
              " "
            ) : (
              <Empty description="Không Hồ sơ PO nào cần chú ý: không hồ sơ nào vượt mốc SLA đã xác nhận, và NCC nào cũng có cập nhật gần đây. Mốc SLA còn chờ xác nhận không được tính." />
            ),
          }}
          footer={
            data && data.length > 0
              ? () => `${data.length} hồ sơ, theo thứ tự máy chủ trả về.`
              : undefined
          }
        />
      )}
    </div>
  );
}
