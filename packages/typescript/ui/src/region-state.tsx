"use client";

import type { ReactNode } from "react";
import { DisconnectOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Result, Skeleton, Typography } from "antd";

/** Why a region could not show its content, as the app read the failure. */
export interface RegionFailure {
  /** The server's machine code (the app's `errorCode()`), null when none. */
  code: string | null;
  /** The server's sentence (the app's `errorMessage()`), never the code. */
  message: string;
  /** The server's `request_id`, for a person reporting the failure. */
  requestId?: string | null;
  /** The request never reached the server. */
  offline?: boolean;
}

export type RegionKind =
  "offline" | "forbidden" | "entitlement" | "notFound" | "conflict" | "error";

/**
 * The one mapping from an error code to the state a region draws
 * (ui-quality §4); anything else is the error state. Entitlement is not
 * forbidden: what the plan includes and what a role may do are separate
 * concerns (CLAUDE.md).
 */
export const REGION_STATE_BY_CODE: Readonly<Record<string, RegionKind>> = {
  permission_denied: "forbidden",
  entitlement_denied: "entitlement",
  not_found: "notFound",
  conflict: "conflict",
};

export function regionKind(failure: RegionFailure): RegionKind {
  if (failure.offline) return "offline";
  return (failure.code && REGION_STATE_BY_CODE[failure.code]) || "error";
}

type Copy = { title?: ReactNode; subTitle?: ReactNode };

export interface RegionStateProps {
  /** Until the first response: a skeleton shaped like the content. */
  loading?: boolean;
  failure?: RegionFailure | null;
  /** What the region holds, in the reader's words ("danh sách Hồ sơ PO"). */
  what: string;
  onRetry?: () => void;
  /** Words for a state where the default sentence is not enough. */
  copy?: Partial<Record<RegionKind, Copy>>;
  /** Skeleton paragraph rows. */
  rows?: number;
  /** Inside a card: an alert in place of a full-page result. */
  compact?: boolean;
}

function defaults(
  what: string,
  failure: RegionFailure,
): Record<RegionKind, Copy> {
  return {
    offline: {
      title: "Mất kết nối mạng",
      subTitle: `Chưa tải được ${what}. Kết nối lại rồi bấm Thử lại.`,
    },
    forbidden: {
      title: `Bạn chưa được xem ${what}`,
      subTitle:
        "Vai của bạn chưa có quyền này. Liên hệ quản trị workspace để được cấp.",
    },
    entitlement: {
      title: `Gói hiện tại chưa mở ${what}`,
      subTitle: failure.message,
    },
    notFound: {
      title: `Không tìm thấy ${what}`,
      subTitle: "Không có trong workspace đang mở.",
    },
    conflict: {
      title: `Có người vừa thay đổi ${what}`,
      subTitle: failure.message,
    },
    error: { title: `Không tải được ${what}`, subTitle: failure.message },
  };
}

const RESULT_STATUS = {
  offline: "warning",
  forbidden: "403",
  entitlement: "info",
  notFound: "404",
  conflict: "warning",
  error: "500",
} as const;

/**
 * Every state of a region that loads on its own except its content and its
 * empty (which is antd `Empty` with the region's own first action): loading,
 * offline, forbidden, the plan's limit, not found, conflict and error, from
 * one table. Renders nothing when the region is ready. Error and offline
 * offer "Thử lại"; error shows the server's sentence and its request id.
 */
export function RegionState({
  loading,
  failure,
  what,
  onRetry,
  copy,
  rows = 4,
  compact,
}: RegionStateProps) {
  if (!failure) {
    return loading ? <Skeleton active paragraph={{ rows }} /> : null;
  }
  const kind = regionKind(failure);
  const { title, subTitle } = {
    ...defaults(what, failure)[kind],
    ...copy?.[kind],
  };
  const retryable =
    kind === "error" || kind === "offline" || kind === "conflict";
  const retry =
    retryable && onRetry ? (
      <Button icon={<ReloadOutlined aria-hidden />} onClick={onRetry}>
        Thử lại
      </Button>
    ) : null;
  const requestId =
    kind === "error" && failure.requestId ? (
      <Typography.Text>Mã yêu cầu: {failure.requestId}</Typography.Text>
    ) : null;

  if (compact) {
    return (
      <Alert
        type={kind === "error" ? "error" : "warning"}
        showIcon
        icon={
          kind === "offline" ? <DisconnectOutlined aria-hidden /> : undefined
        }
        title={title}
        description={
          <>
            {subTitle}
            {requestId ? <div>{requestId}</div> : null}
          </>
        }
        action={retry}
      />
    );
  }
  return (
    <Result
      status={RESULT_STATUS[kind]}
      icon={kind === "offline" ? <DisconnectOutlined aria-hidden /> : undefined}
      title={title}
      subTitle={
        <>
          {subTitle}
          {requestId ? <div>{requestId}</div> : null}
        </>
      }
      extra={retry}
    />
  );
}
