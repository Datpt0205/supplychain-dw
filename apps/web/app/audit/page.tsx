"use client";

import { useCallback, useState } from "react";
import { Button, Card, Flex, Input, Table, Tag, Typography } from "antd";
import { AuditOutlined, ReloadOutlined } from "@ant-design/icons";
import type { AuditEvent } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../components/load-error";
import { LoadMore } from "../../components/load-more";
import { formatDateTime } from "../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../lib/directory";
import { apiClient } from "../../lib/session";
import { useCachedPages } from "../../lib/use-cached-pages";

const ACTIONS: Record<string, { label: string; color: string }> = {
  "run.started": { label: "Bắt đầu lượt chạy", color: "processing" },
  "run.waiting_approval": { label: "Chờ duyệt", color: "warning" },
  "run.resumed": { label: "Chạy tiếp", color: "processing" },
  "run.completed": { label: "Xong lượt chạy", color: "success" },
  "approval.decided": { label: "Đã quyết yêu cầu", color: "success" },
  "tool.executed": { label: "Đã chạy công cụ", color: "default" },
  // PLUG-IN POINT: a bounded context adds its own actions here. An unmapped
  // action still shows, as "Hoạt động hệ thống" — a row is never dropped.
};
const OTHER_ACTION = { label: "Hoạt động hệ thống", color: "default" };

export default function AuditPage() {
  const members = useWorkspaceMembers();
  const [filter, setFilter] = useState("");

  const { items, loading, loadingMore, error, hasMore, loadMore, reload } =
    useCachedPages(
      "audit:events",
      useCallback(
        (cursor: string | null) => apiClient().listAuditEvents({ cursor }),
        [],
      ),
    );

  const visible = items.filter(
    (event) =>
      !filter ||
      event.action.includes(filter) ||
      event.resource_type.includes(filter) ||
      event.resource_id.includes(filter) ||
      (memberName(members, event.actor_id) ?? "")
        .toLowerCase()
        .includes(filter.toLowerCase()),
  );

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        icon={<AuditOutlined />}
        title="Nhật ký kiểm toán"
        subtitle="Lịch sử được ghi liên tục và không ai sửa được, kể cả quản trị viên."
        actions={
          <>
            <Input.Search
              allowClear
              className="w-56"
              aria-label="Tìm trong nhật ký"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Tìm thao tác hoặc bản ghi…"
            />
            <Button
              icon={<ReloadOutlined aria-hidden />}
              aria-label="Tải lại"
              onClick={reload}
            />
          </>
        }
      />
      <Flex vertical gap="middle">
        {error != null && <LoadError error={error} onRetry={reload} />}
        {loading && error == null && <RegionState kind="loading" />}
        {!loading && error == null && visible.length === 0 && (
          <RegionState kind="empty" title="Không có sự kiện nào khớp" />
        )}
        {visible.length > 0 && (
          <Card>
            <Table<AuditEvent>
              rowKey={(event, index) =>
                `${event.occurred_at}-${event.action}-${index}`
              }
              size="small"
              pagination={false}
              scroll={{ x: "max-content" }}
              dataSource={visible}
              columns={[
                {
                  title: "Lúc",
                  dataIndex: "occurred_at",
                  render: (value: string) => formatDateTime(value),
                },
                {
                  title: "Ai",
                  dataIndex: "actor_id",
                  // A name, falling back to a dash when the roster has no such
                  // member — somebody who has since left still shows a row.
                  render: (value: string | null) =>
                    memberName(members, value) ?? "—",
                },
                {
                  title: "Thao tác",
                  dataIndex: "action",
                  render: (value: string) => {
                    const action = ACTIONS[value] ?? OTHER_ACTION;
                    return <Tag color={action.color}>{action.label}</Tag>;
                  },
                },
                {
                  title: "Bản ghi",
                  key: "resource",
                  render: (_, event) => (
                    <Flex vertical>
                      <Typography.Text type="secondary" className="text-xs">
                        {event.resource_type}
                      </Typography.Text>
                      <Typography.Text code className="text-xs">
                        {event.resource_id}
                      </Typography.Text>
                    </Flex>
                  ),
                },
                {
                  title: "Quyết định chính sách",
                  dataIndex: "policy_decision",
                  render: (value: string | null) => value ?? "—",
                },
                {
                  title: "Mã truy vết",
                  dataIndex: "trace_id",
                  render: (value: string | null) =>
                    value ? (
                      <Typography.Text code>{value}</Typography.Text>
                    ) : (
                      "—"
                    ),
                },
                {
                  title: "Chi tiết",
                  dataIndex: "details",
                  ellipsis: true,
                  width: 260,
                  render: (value: AuditEvent["details"]) =>
                    Object.keys(value).length > 0 ? JSON.stringify(value) : "—",
                },
              ]}
            />
          </Card>
        )}
        {!loading && error == null && (
          <LoadMore
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
            shown={items.length}
            noun="sự kiện"
            filtered={filter.length > 0}
          />
        )}
      </Flex>
    </div>
  );
}
