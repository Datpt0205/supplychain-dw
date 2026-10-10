"use client";

import { useCallback } from "react";
import { Card, Flex, Tag, Typography } from "antd";
import { BulbOutlined } from "@ant-design/icons";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../components/load-error";
import { LoadMore } from "../../components/load-more";
import { apiClient } from "../../lib/session";
import { useCachedPages } from "../../lib/use-cached-pages";

const TYPE_LABELS: Record<string, string> = {
  episodic: "Sự việc",
  semantic: "Kiến thức",
  procedural: "Quy trình",
  preference: "Ưu tiên",
  commitment: "Cam kết",
};

export default function MemoryPage() {
  const { items, loading, loadingMore, error, hasMore, loadMore, reload } =
    useCachedPages(
      "memory:items",
      useCallback(
        (cursor: string | null) => apiClient().listMemoryItems({ cursor }),
        [],
      ),
    );

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        icon={<BulbOutlined />}
        title="Bộ nhớ dài hạn"
        subtitle="Chỉ thông tin có bằng chứng rõ và đạt chính sách mới được nhớ lâu dài."
      />
      <Flex vertical gap="small">
        {error != null && <LoadError error={error} onRetry={reload} />}
        {loading && error == null && <RegionState kind="loading" />}
        {!loading && error == null && items.length === 0 && (
          <RegionState
            kind="empty"
            title="Chưa có ký ức dài hạn nào"
            description="Mục có bằng chứng hiện ở đây sau khi hệ thống xong một lượt chạy đủ điều kiện."
          />
        )}
        {items.map((item) => (
          <Card key={item.memory_id} size="small">
            <Flex vertical gap={6}>
              <Flex wrap justify="space-between" align="center" gap="small">
                <Tag>{TYPE_LABELS[item.memory_type] ?? "Khác"}</Tag>
                <Typography.Text type="secondary" className="text-xs">
                  độ tin cậy {(item.confidence * 100).toFixed(0)}% ·{" "}
                  {item.provenance_count} nguồn
                </Typography.Text>
              </Flex>
              <Typography.Text>{item.content}</Typography.Text>
              <Typography.Text type="secondary" className="text-xs">
                Lượt chạy:{" "}
                <Typography.Text code className="break-all text-xs">
                  {item.created_by_run_id}
                </Typography.Text>
              </Typography.Text>
            </Flex>
          </Card>
        ))}
        {!loading && items.length > 0 && (
          <LoadMore
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
            shown={items.length}
            noun="ký ức"
          />
        )}
      </Flex>
    </div>
  );
}
