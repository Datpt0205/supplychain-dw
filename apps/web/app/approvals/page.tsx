"use client";

import { useCallback, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Skeleton,
  Table,
  Tooltip,
  Typography,
  type TableColumnsType,
} from "antd";
import {
  CheckOutlined,
  CloseOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import type { Approval } from "@dw/contracts";
import Link from "next/link";
import { PageHeader } from "@dw/ui";
import { ApprovalStatusTag } from "../../components/approval-status-tag";
import {
  ToolApprovalPayload,
  approvalTitle,
} from "../../components/tool-approval";
import { approvalClient } from "../../lib/approvals/registry";
import { useAuth } from "../../lib/auth/auth-context";
import { formatDateTime, formatDateTimeFull, VN_TIME } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { useCachedPages } from "../../lib/use-cached-pages";

/** Why Duyệt and Từ chối wait on a strict type with no comment yet. */
const COMMENT_REQUIRED_REASON =
  "Loại yêu cầu này cần nhận xét: nhập nhận xét rồi mới quyết được.";

export default function ApprovalsPage() {
  const { hasScope } = useAuth();
  const [comments, setComments] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canDecide = hasScope("approvals.decide");

  const {
    items: approvals,
    loading,
    loadingMore,
    error: loadError,
    hasMore,
    loadMore,
    reload,
  } = useCachedPages(
    "approvals:pending",
    useCallback(
      (cursor: string | null) => apiClient().listApprovals({ cursor }),
      [],
    ),
  );

  /**
   * The scope stamped on this approval that keeps the viewer from deciding it
   * (ADR 0020), or null. The server says whether they may (`can_decide`, the
   * decision's own checks); the session's `hasScope` is not asked, because it
   * lets `platform_admin` pass a scope a stamped approval does not. The server
   * lists a stamped approval only to who may decide it and to its requester
   * (ADR 0004, amendment 2026-10-07), so in practice this locks the
   * requester's own; the lock stays for any other answer the server gives.
   */
  function missingScope(approval: Approval): string | null {
    return approval.can_decide ? null : approval.required_scope;
  }

  /** A strict approval type refuses a blank comment server-side; say so here. */
  function missingComment(approval: Approval): boolean {
    return approval.requires_comment && !(comments[approval.id] ?? "").trim();
  }

  // The decision resumes a checkpointed run, and graphs are registered per
  // process — so the client is picked from the approval's own type, never
  // assumed to be the platform API.
  async function decide(approval: Approval, approve: boolean) {
    setBusyId(approval.id);
    try {
      await approvalClient(approval.approval_type).decideApproval(approval.id, {
        approve,
        comment: comments[approval.id] ?? "",
      });
      setComments((current) => ({ ...current, [approval.id]: "" }));
      setError(null);
      reload();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusyId(null);
    }
  }

  const pending = approvals.filter((item) => item.status === "pending");
  const decided = approvals.filter((item) => item.status !== "pending");

  const decidedColumns: TableColumnsType<Approval> = [
    {
      title: `Quyết lúc (${VN_TIME})`,
      dataIndex: "decided_at",
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "Loại",
      dataIndex: "approval_type",
      render: (value: string) => (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    {
      title: "Lý do",
      dataIndex: "reason",
      render: (value: string) => (
        <Typography.Text ellipsis={{ tooltip: value }} className="max-w-64">
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "Run",
      dataIndex: "run_id",
      responsive: ["lg"],
      render: (value: string | null) =>
        value ? <Typography.Text code>{value}</Typography.Text> : "—",
    },
    {
      title: "Kết quả",
      dataIndex: "status",
      render: (value: Approval["status"]) => (
        <ApprovalStatusTag status={value} />
      ),
    },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Duyệt"
        description="Một việc muốn thay đổi thứ gì đó bên ngoài hệ thống dừng ở đây tới khi một người quyết. Chưa việc nào trên trang này đã xảy ra."
        extra={
          <Button icon={<ReloadOutlined aria-hidden />} onClick={reload}>
            Làm mới
          </Button>
        }
      />
      <Flex vertical gap="middle">
        {(error ?? loadError) != null && (
          <Alert
            type="error"
            showIcon
            title={error ?? errorMessage(loadError)}
          />
        )}
        {loading && loadError == null && (
          <Skeleton active paragraph={{ rows: 6 }} />
        )}
        {!loading && pending.length === 0 && (
          <Empty description="Không có yêu cầu nào chờ quyết. Một yêu cầu hiện ở đây ngay khi một việc tới bước mà chính sách của nó không cho tự làm." />
        )}

        {pending.map((approval) => {
          const lacking = missingScope(approval);
          const lockReason =
            lacking === null
              ? null
              : `Chỉ người có quyền ${lacking} được quyết yêu cầu này`;
          // Withdrawing your own request is not deciding it: the server lets
          // the requester reject without the stamped scope, so the page does
          // too.
          const approveLocked = lacking !== null;
          const rejectLocked = lacking !== null && !approval.requested_by_me;
          // A strict type waits for its comment; the buttons say so rather
          // than sit greyed out with only a placeholder to explain them.
          const commentLock = missingComment(approval)
            ? COMMENT_REQUIRED_REASON
            : null;
          // A disabled button takes no pointer events, so the tooltip hangs
          // on a wrapper; the same sentence also sits beside the buttons as
          // text.
          const withLock = (reason: string | null, button: ReactNode) =>
            reason ? (
              <Tooltip title={reason}>
                <span className="inline-flex">{button}</span>
              </Tooltip>
            ) : (
              button
            );
          return (
            <Card
              key={approval.id}
              size="small"
              title={
                <Flex wrap gap="small" align="center">
                  <Link href={`/approvals/${approval.id}`}>
                    {approvalTitle(approval.approval_type)}
                  </Link>
                  <ApprovalStatusTag status={approval.status} />
                </Flex>
              }
            >
              <Flex vertical gap="middle">
                <Flex vertical gap={2}>
                  <Typography.Text>{approval.reason}</Typography.Text>
                  <Typography.Text type="secondary">
                    <Typography.Text code>
                      {approval.approval_type}
                    </Typography.Text>{" "}
                    · tạo lúc {formatDateTimeFull(approval.created_at)}
                  </Typography.Text>
                </Flex>
                <ToolApprovalPayload payload={approval.payload} />
                {!canDecide ? (
                  <Typography.Text>
                    Vai của bạn không có quyền approvals.decide, nên yêu cầu này
                    chỉ để xem.
                  </Typography.Text>
                ) : (
                  <Flex vertical gap="small">
                    <Input.TextArea
                      aria-label={`Nhận xét cho ${approvalTitle(approval.approval_type)}`}
                      rows={2}
                      value={comments[approval.id] ?? ""}
                      onChange={(event) =>
                        setComments((current) => ({
                          ...current,
                          [approval.id]: event.target.value,
                        }))
                      }
                      placeholder={
                        approval.requires_comment
                          ? "Nhận xét giải thích quyết định (bắt buộc)"
                          : "Nhận xét giải thích quyết định (không bắt buộc)"
                      }
                    />
                    <Flex wrap gap="small" align="center">
                      {withLock(
                        approveLocked ? lockReason : commentLock,
                        <Button
                          type="primary"
                          icon={<CheckOutlined aria-hidden />}
                          loading={busyId === approval.id}
                          onClick={() => void decide(approval, true)}
                          disabled={
                            approveLocked ||
                            busyId === approval.id ||
                            missingComment(approval)
                          }
                        >
                          Duyệt
                        </Button>,
                      )}
                      {withLock(
                        rejectLocked ? lockReason : commentLock,
                        <Button
                          danger
                          icon={<CloseOutlined aria-hidden />}
                          onClick={() => void decide(approval, false)}
                          disabled={
                            rejectLocked ||
                            busyId === approval.id ||
                            missingComment(approval)
                          }
                        >
                          Từ chối
                        </Button>,
                      )}
                      {lockReason !== null ? (
                        <Typography.Text>
                          {lockReason}
                          {approval.requested_by_me &&
                            ". Bạn vẫn rút được yêu cầu của mình."}
                        </Typography.Text>
                      ) : (
                        commentLock !== null && (
                          <Typography.Text>{commentLock}</Typography.Text>
                        )
                      )}
                    </Flex>
                  </Flex>
                )}
              </Flex>
            </Card>
          );
        })}

        {decided.length > 0 && (
          <Card size="small" title="Đã quyết gần đây">
            <Table<Approval>
              rowKey="id"
              size="small"
              pagination={false}
              sticky
              scroll={{ x: "max-content" }}
              columns={decidedColumns}
              dataSource={decided}
              footer={() =>
                "Đã quyết; giữ ở đây để lần ngược một việc về người đã cho nó chạy."
              }
            />
          </Card>
        )}

        {!loading && pending.length > 0 && (
          <Flex justify="space-between" align="center" wrap gap="small">
            <Typography.Text role="status">
              {hasMore
                ? `Đã hiện ${approvals.length} yêu cầu; còn nữa.`
                : `Đã hiện tất cả ${approvals.length} yêu cầu.`}
            </Typography.Text>
            {hasMore && (
              <Button loading={loadingMore} onClick={loadMore}>
                Tải thêm
              </Button>
            )}
          </Flex>
        )}
      </Flex>
    </div>
  );
}
