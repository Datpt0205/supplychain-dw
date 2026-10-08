"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Flex, Tag, Typography } from "antd";
import { ApiOutlined } from "@ant-design/icons";
import type { Integration } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../components/load-error";
import { apiClient } from "../../lib/session";

const SIDE_EFFECT: Record<string, string> = {
  none: "Không ghi gì",
  internal: "Ghi trong hệ thống",
  external: "Ra ngoài hệ thống",
  critical: "Nghiêm trọng",
};

const APPROVAL: Record<string, string> = {
  never: "Không cần duyệt",
  conditional: "Duyệt khi có điều kiện",
  always: "Luôn cần duyệt",
};

export default function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setError(null);
    apiClient()
      .listIntegrations()
      .then(setIntegrations)
      .catch((e: unknown) => setError(e));
  }, []);
  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        icon={<ApiOutlined />}
        title="Tích hợp và kết nối"
        subtitle="Danh mục công cụ mà bộ thực thi chạy theo chính sách, quyền và giới hạn của bản phát hành hiện tại."
      />
      {error != null && <LoadError error={error} onRetry={load} />}
      {integrations === null && error == null && <RegionState kind="loading" />}
      {integrations?.length === 0 && (
        <RegionState
          kind="empty"
          title="Chưa có kết nối nào"
          description="Danh sách hiện ra khi có một kết nối được đăng ký."
        />
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {integrations?.map((integration) => (
          <Card
            key={`${integration.tool}@${integration.version}`}
            size="small"
            title={
              <span>
                {integration.tool}{" "}
                <Typography.Text type="secondary" code>
                  v{integration.version}
                </Typography.Text>
              </span>
            }
          >
            <Flex vertical gap="small">
              <Typography.Text>{integration.description}</Typography.Text>
              <Flex wrap gap={4}>
                <Tag color="volcano">
                  {SIDE_EFFECT[integration.side_effect_level] ??
                    integration.side_effect_level}
                </Tag>
                <Tag color="gold">
                  {APPROVAL[integration.approval_policy] ??
                    integration.approval_policy}
                </Tag>
                {integration.idempotent && (
                  <Tag color="green">Chạy lại an toàn</Tag>
                )}
                <Tag>Tối đa {integration.timeout_seconds} giây</Tag>
              </Flex>
              <Typography.Text type="secondary" className="text-xs">
                Quyền cần:{" "}
                {integration.required_scopes.length > 0
                  ? integration.required_scopes.map((scope) => (
                      <Typography.Text key={scope} code className="text-xs">
                        {scope}
                      </Typography.Text>
                    ))
                  : "—"}
              </Typography.Text>
            </Flex>
          </Card>
        ))}
      </div>
    </div>
  );
}
