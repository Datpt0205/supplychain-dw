"use client";

import type { ReactNode } from "react";
import {
  ClockCircleOutlined,
  DisconnectOutlined,
  ExclamationCircleOutlined,
  FileSearchOutlined,
  InboxOutlined,
  LockOutlined,
  StopOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { Flex, Skeleton, Typography, theme } from "antd";
import type { ErrorCodeValue } from "@dw/contracts";

/** What a page or a region is showing instead of its content. */
export type RegionKind =
  | "loading"
  | "empty"
  | "error"
  | "forbidden"
  | "notfound"
  | "conflict"
  | "entitlement"
  | "session"
  | "offline";

/**
 * The server's error code, mapped to what the region shows: the ONLY place
 * that mapping is made. `satisfies` makes a code added to the contract and not
 * here a type error, not a silent fall-through.
 */
export const ERROR_STATE = {
  validation_failed: "error",
  not_found: "notfound",
  conflict: "conflict",
  unauthenticated: "session",
  permission_denied: "forbidden",
  entitlement_denied: "entitlement",
  approval_required: "error",
  tenant_context_missing: "session",
  idempotency_conflict: "conflict",
  rate_limited: "error",
  payload_too_large: "error",
  unsupported_media_type: "error",
  timeout: "error",
  upstream_unavailable: "error",
  internal: "error",
} as const satisfies Record<ErrorCodeValue, RegionKind>;

export interface RegionError {
  code: string;
  /** The server's sentence, already written for a person. */
  message: string;
  requestId?: string | null;
}

/**
 * The region for a failed call. A code this build does not know (a server
 * newer than the web) is an `error`, never content.
 */
export function stateForError(error: RegionError | "offline"): {
  kind: RegionKind;
  message?: string;
  requestId?: string | null;
} {
  if (error === "offline") return { kind: "offline" };
  const kind = Object.prototype.hasOwnProperty.call(ERROR_STATE, error.code)
    ? ERROR_STATE[error.code as ErrorCodeValue]
    : "error";
  return { kind, message: error.message, requestId: error.requestId };
}

const DEFAULTS: Record<
  Exclude<RegionKind, "loading">,
  { title: string; icon: ReactNode }
> = {
  empty: { title: "Chưa có gì ở đây", icon: <InboxOutlined /> },
  error: {
    title: "Không tải được dữ liệu",
    icon: <ExclamationCircleOutlined />,
  },
  forbidden: {
    title: "Bạn không có quyền xem mục này",
    icon: <LockOutlined />,
  },
  notfound: { title: "Không tìm thấy", icon: <FileSearchOutlined /> },
  conflict: {
    title: "Dữ liệu vừa được người khác thay đổi",
    icon: <SwapOutlined />,
  },
  entitlement: {
    title: "Gói dịch vụ chưa gồm tính năng này",
    icon: <StopOutlined />,
  },
  session: {
    title: "Phiên làm việc đã hết, hãy đăng nhập lại",
    icon: <ClockCircleOutlined />,
  },
  offline: { title: "Mất kết nối mạng", icon: <DisconnectOutlined /> },
};

export interface RegionStateProps {
  kind: RegionKind;
  /** Overrides the kind's default title. */
  title?: ReactNode;
  description?: ReactNode;
  /** The server's request id, shown on an `error` so support can find it. */
  requestId?: string | null;
  /** Buttons: a retry, a way back. */
  action?: ReactNode;
  /** Inside a card or a table rather than a whole page. */
  compact?: boolean;
}

/**
 * One component for what a page or a region shows when it is not showing its
 * data: loading, empty, and every failure the server names. An `error` is an
 * alert (read out at once); the rest are status.
 */
export function RegionState({
  kind,
  title,
  description,
  requestId,
  action,
  compact = false,
}: RegionStateProps) {
  const { token } = theme.useToken();
  if (kind === "loading") {
    return (
      <div role="status" aria-label="Đang tải" aria-busy="true">
        <Skeleton active paragraph={{ rows: compact ? 2 : 5 }} />
      </div>
    );
  }
  const preset = DEFAULTS[kind];
  return (
    <Flex
      vertical
      align="center"
      gap={8}
      role={kind === "error" ? "alert" : "status"}
      className={`text-center ${compact ? "px-4 py-6" : "rounded-xl border border-dashed px-6 py-12"}`}
      style={compact ? undefined : { background: token.colorBgContainer }}
    >
      <span
        aria-hidden
        style={{
          fontSize: compact ? token.fontSizeHeading4 : token.fontSizeHeading2,
          color:
            kind === "error" ? token.colorErrorText : token.colorTextTertiary,
        }}
      >
        {preset.icon}
      </span>
      <Typography.Text strong>{title ?? preset.title}</Typography.Text>
      {description && (
        <Typography.Text type="secondary" className="max-w-md">
          {description}
        </Typography.Text>
      )}
      {kind === "error" && requestId && (
        <Typography.Text type="secondary">
          Mã yêu cầu: <Typography.Text code>{requestId}</Typography.Text>
        </Typography.Text>
      )}
      {action && (
        <Flex wrap gap="small" justify="center" className="mt-2">
          {action}
        </Flex>
      )}
    </Flex>
  );
}
