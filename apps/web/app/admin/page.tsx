"use client";

import { useEffect, useState } from "react";
import { Card, Flex, Tag, Typography } from "antd";
import { CheckCircleFilled, SafetyOutlined } from "@ant-design/icons";
import type { AdminRole } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../components/load-error";
import { MembersManager } from "../../components/admin/members-manager";
import { useAuth } from "../../lib/auth/auth-context";
import { roleLabel } from "../../lib/nav/roles";
import { apiClient } from "../../lib/session";

/**
 * Who holds which role in this workspace, and what each role grants. The role
 * catalog is read from the API (`platform.roles` owns it); this page restates
 * none of it, only how a role key reads to a person (`lib/nav/roles.ts`).
 */
export default function AdminPage() {
  const { displayName, active, roles, scopes, hasScope } = useAuth();
  const canManageMembers = hasScope("platform.members.write");
  const canReadCatalog = hasScope("platform.roles.read");

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        icon={<SafetyOutlined />}
        title="Vai trò và quyền"
        subtitle="Quyền trong workspace được cấp theo vai của từng thành viên."
      />
      <Flex vertical gap="middle">
        {canManageMembers && <MembersManager />}

        <Card
          title="Quyền của bạn"
          extra={
            <Typography.Text type="secondary">
              {displayName}
              {active ? ` · ${active.workspaceName}` : ""}
            </Typography.Text>
          }
        >
          <Flex vertical gap="small">
            <Flex wrap gap={6} align="center">
              <Typography.Text type="secondary">Vai:</Typography.Text>
              {roles.length > 0
                ? roles.map((r) => <Tag key={r}>{roleLabel(r)}</Tag>)
                : "—"}
            </Flex>
            <Flex wrap gap={6} align="center">
              <Typography.Text type="secondary">
                Quyền được cấp:
              </Typography.Text>
              {scopes.length > 0
                ? scopes.map((s) => (
                    <Tag key={s} className="font-mono">
                      {s}
                    </Tag>
                  ))
                : "—"}
            </Flex>
          </Flex>
        </Card>

        {canReadCatalog && <RoleCatalog mine={roles} />}
      </Flex>
    </div>
  );
}

function RoleCatalog({ mine }: { mine: string[] }) {
  const [catalog, setCatalog] = useState<AdminRole[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    apiClient()
      .listAdminRoles()
      .then(setCatalog)
      .catch((e: unknown) => setError(e));
  }, []);

  if (error != null) return <LoadError error={error} />;
  if (catalog === null) return <RegionState kind="loading" compact />;
  return (
    <div className="grid gap-4 md:grid-cols-3">
      {catalog.map((role) => (
        <Card
          key={role.key}
          size="small"
          title={roleLabel(role.key, role.name)}
          extra={
            mine.includes(role.key) ? (
              <CheckCircleFilled aria-label="Vai của bạn" />
            ) : null
          }
        >
          <Flex wrap gap={4}>
            {role.scopes.map((s) => (
              <Tag key={s} className="font-mono">
                {s}
              </Tag>
            ))}
          </Flex>
        </Card>
      ))}
    </div>
  );
}
