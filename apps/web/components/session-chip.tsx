"use client";

import Link from "next/link";
import {
  Avatar,
  Button,
  Dropdown,
  Flex,
  Tag,
  Typography,
  type MenuProps,
} from "antd";
import {
  AuditOutlined,
  DownOutlined,
  LogoutOutlined,
  SettingOutlined,
} from "@ant-design/icons";
import { useAuth } from "../lib/auth/auth-context";
import { NAV_ITEMS } from "../lib/nav/registry";
import { OPERATOR_LABEL, roleLabel } from "../lib/nav/roles";

// Seniority low → high. Only the most senior role is shown, by its full name,
// so two different admins never both read as a bare "admin". A platform
// operator outranks every in-tenant role. A role a bounded context adds is
// unranked and therefore only shown when the person holds nothing else.
const ROLE_RANK = ["member", "approver", "org_admin", "platform_admin"];

// The menu's second door to the audit trail reads its scope from the nav
// registry's entry, the one owner of "who is offered /audit": hard-coding a
// scope here kept offering every member a page the API refuses
// (approval-audit-and-workspace/02).
const AUDIT_LOG = NAV_ITEMS.find((item) => item.href === "/audit");

function tagColor(role: string | undefined, operator: boolean): string {
  if (operator || role === "platform_admin" || role === "org_admin")
    return "gold";
  if (role === "approver") return "green";
  return "default";
}

/** Header chip: who is signed in, where to go next, and sign-out. */
export function SessionChip() {
  const { status, displayName, roles, isPlatformOperator, logout, hasScope } =
    useAuth();

  if (status !== "ready") return null;

  const topRole = [...roles].sort(
    (a, b) => ROLE_RANK.indexOf(b) - ROLE_RANK.indexOf(a),
  )[0];
  // A platform operator (creates tenants) outranks any in-tenant role.
  const roleText = isPlatformOperator
    ? OPERATOR_LABEL
    : topRole
      ? roleLabel(topRole)
      : null;
  const color = tagColor(topRole, isPlatformOperator);
  const name = displayName || "Người dùng";

  const items: MenuProps["items"] = [
    {
      key: "who",
      type: "group",
      label: (
        <Flex vertical gap={4}>
          <Typography.Text strong ellipsis>
            {name}
          </Typography.Text>
          {roleText && (
            <span>
              <Tag color={color}>{roleText}</Tag>
            </span>
          )}
        </Flex>
      ),
    },
    { type: "divider" },
    {
      key: "settings",
      icon: <SettingOutlined aria-hidden />,
      label: <Link href="/settings">Cài đặt cá nhân</Link>,
    },
    ...(AUDIT_LOG && (!AUDIT_LOG.scope || hasScope(AUDIT_LOG.scope))
      ? [
          {
            key: "audit",
            icon: <AuditOutlined aria-hidden />,
            label: <Link href={AUDIT_LOG.href}>{AUDIT_LOG.label}</Link>,
          },
        ]
      : []),
    {
      key: "logout",
      icon: <LogoutOutlined aria-hidden />,
      danger: true,
      label: "Đăng xuất",
      onClick: logout,
    },
  ];

  return (
    <Dropdown menu={{ items }} trigger={["click"]} placement="bottomRight">
      <Button type="text" className="!px-1.5" aria-label={`Tài khoản: ${name}`}>
        <Avatar size="small">{name.charAt(0).toUpperCase()}</Avatar>
        {/* Not between lg and xl: the horizontal menu needs that width, and
            at 992 px every item had overflowed into "…". */}
        <span className="hidden max-w-[9rem] truncate sm:inline lg:hidden xl:inline">
          {name}
        </span>
        {roleText && (
          <Tag
            color={color}
            className="!me-0 hidden md:inline-block lg:hidden xl:inline-block"
          >
            {roleText}
          </Tag>
        )}
        <DownOutlined aria-hidden className="text-xs" />
      </Button>
    </Dropdown>
  );
}
