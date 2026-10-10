"use client";

import { Button, Dropdown, Flex, Typography, type MenuProps } from "antd";
import { ApartmentOutlined, SwapOutlined } from "@ant-design/icons";
import { useAuth } from "../lib/auth/auth-context";

function Label({ tenant, workspace }: { tenant: string; workspace: string }) {
  return (
    <Flex vertical className="min-w-0 text-left leading-tight">
      <Typography.Text strong ellipsis className="text-xs">
        {tenant}
      </Typography.Text>
      <Typography.Text type="secondary" ellipsis className="text-[11px]">
        {workspace}
      </Typography.Text>
    </Flex>
  );
}

/**
 * The active company + workspace, in the top navbar. When the signed-in user
 * belongs to more than one workspace it becomes a picker to switch between them
 * (auth context re-activates the chosen membership). A single membership just
 * shows, no dropdown. Nothing renders for an operator with no workspace.
 */
export function WorkspaceSwitcher() {
  const { active, memberships, selectWorkspace } = useAuth();
  if (!active) return null;

  const current = (
    <Flex align="center" gap={8} className="min-w-0">
      <ApartmentOutlined aria-hidden />
      <Label tenant={active.tenantName} workspace={active.workspaceName} />
    </Flex>
  );

  if (memberships.length <= 1) {
    return (
      <div
        className="min-w-0 max-w-[15rem]"
        title={`${active.tenantName} · ${active.workspaceName}`}
      >
        {current}
      </div>
    );
  }

  const items: MenuProps["items"] = memberships.map((m) => ({
    key: m.workspaceId,
    label: <Label tenant={m.tenantName} workspace={m.workspaceName} />,
  }));

  return (
    <Dropdown
      trigger={["click"]}
      menu={{
        items,
        selectable: true,
        selectedKeys: [active.workspaceId],
        onClick: ({ key }) => selectWorkspace(key),
      }}
    >
      <Button
        className="h-auto min-w-0 max-w-[15rem] py-1"
        aria-label={`Workspace: ${active.tenantName} · ${active.workspaceName}. Đổi workspace`}
      >
        {current}
        <SwapOutlined aria-hidden className="text-xs" />
      </Button>
    </Dropdown>
  );
}
