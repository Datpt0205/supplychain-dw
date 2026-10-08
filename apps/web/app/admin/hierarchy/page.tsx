"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, Card, Flex, Select, Spin, Typography, theme } from "antd";
import { ApartmentOutlined } from "@ant-design/icons";
import type { HierarchyMember } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../../lib/session";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage as errorText } from "../../../lib/error-message";
import { roleLabels } from "../../../lib/nav/roles";

export default function HierarchyPage() {
  const { hasScope } = useAuth();

  if (!hasScope("platform.members.write")) {
    return (
      <RegionState
        kind="forbidden"
        description="Cần quyền quản lý thành viên để xem trang này."
      />
    );
  }
  return <HierarchyManager />;
}

function HierarchyManager() {
  const [members, setMembers] = useState<HierarchyMember[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setMembers(await apiClient().listHierarchy());
    } catch (e) {
      setMembers([]);
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setManager = useCallback(
    async (userId: string, managerUserId: string | null) => {
      setSavingId(userId);
      setError(null);
      try {
        await apiClient().setManager(userId, managerUserId);
        await refresh();
      } catch (e) {
        setError(errorText(e));
      } finally {
        setSavingId(null);
      }
    },
    [refresh],
  );

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        icon={<ApartmentOutlined />}
        title="Tuyến báo cáo"
        subtitle="Ai báo cáo cho ai trong workspace. Đổi ô “Báo cáo cho” để đặt người quản lý của một người."
      />
      <Flex vertical gap="middle">
        {error && <Alert type="error" showIcon title={error} />}
        <Card title="Sơ đồ báo cáo">
          <Typography.Paragraph type="secondary">
            Người không có quản lý đứng ở gốc; người báo cáo thụt vào bên dưới.
          </Typography.Paragraph>
          {members === null ? (
            <RegionState kind="loading" compact />
          ) : members.length === 0 ? (
            <RegionState
              kind="empty"
              compact
              title="Chưa có thành viên"
              description="Workspace này chưa có ai để xếp tuyến báo cáo."
            />
          ) : (
            <HierarchyTree
              members={members}
              savingId={savingId}
              onSetManager={setManager}
            />
          )}
        </Card>
      </Flex>
    </div>
  );
}

function HierarchyTree({
  members,
  savingId,
  onSetManager,
}: {
  members: HierarchyMember[];
  savingId: string | null;
  onSetManager: (userId: string, managerUserId: string | null) => void;
}) {
  const byId = new Map(members.map((m) => [m.user_id, m]));
  const childrenOf = (managerId: string | null): HierarchyMember[] =>
    members.filter((m) => {
      // A member whose manager is not in this roster is treated as a root, so
      // nobody is dropped from the tree by a dangling reference.
      const parent = m.manager_user_id;
      if (managerId === null) return parent === null || !byId.has(parent);
      return parent === managerId;
    });

  const renderNodes = (
    managerId: string | null,
    depth: number,
    seen: Set<string>,
  ): ReactNode =>
    childrenOf(managerId).map((m) => {
      // Guard against a cycle in the data (the API forbids one, but never trust
      // that a fetched shape is acyclic before recursing on it).
      if (seen.has(m.user_id)) return null;
      const next = new Set(seen).add(m.user_id);
      return (
        <div key={m.user_id}>
          <MemberRow
            member={m}
            others={members.filter((o) => o.user_id !== m.user_id)}
            depth={depth}
            saving={savingId === m.user_id}
            onSetManager={onSetManager}
          />
          {renderNodes(m.user_id, depth + 1, next)}
        </div>
      );
    });

  return <div>{renderNodes(null, 0, new Set<string>())}</div>;
}

function MemberRow({
  member,
  others,
  depth,
  saving,
  onSetManager,
}: {
  member: HierarchyMember;
  others: HierarchyMember[];
  depth: number;
  saving: boolean;
  onSetManager: (userId: string, managerUserId: string | null) => void;
}) {
  const { token } = theme.useToken();
  const selectId = `manager-${member.user_id}`;
  return (
    <Flex
      wrap
      justify="space-between"
      align="center"
      gap="small"
      className="py-2.5 pr-1"
      style={{
        paddingLeft: depth * 24 + 4,
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <div className="min-w-0">
        <Typography.Text strong ellipsis className="block">
          {member.display_name}
        </Typography.Text>
        <Typography.Text type="secondary" className="text-xs">
          {member.role_keys.length > 0 ? roleLabels(member.role_keys) : "—"}
        </Typography.Text>
      </div>
      <Flex align="center" gap="small">
        <label htmlFor={selectId}>
          <Typography.Text type="secondary" className="text-xs">
            Báo cáo cho
          </Typography.Text>
        </label>
        <Select
          id={selectId}
          className="w-48"
          value={member.manager_user_id ?? ""}
          disabled={saving}
          onChange={(value: string) =>
            onSetManager(member.user_id, value === "" ? null : value)
          }
          options={[
            { value: "", label: "— (không ai)" },
            ...others.map((o) => ({ value: o.user_id, label: o.display_name })),
          ]}
        />
        {saving && <Spin size="small" />}
      </Flex>
    </Flex>
  );
}
