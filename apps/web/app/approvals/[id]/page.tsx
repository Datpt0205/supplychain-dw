"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Flex,
  Input,
  Skeleton,
  Tooltip,
  Typography,
} from "antd";
import { MessageOutlined } from "@ant-design/icons";
import type { Approval, ApprovalViewOutcome } from "@dw/contracts";
import { PageHeader, RegionState, type RegionFailure } from "@dw/ui";
import { ApprovalStatusTag } from "../../../components/approval-status-tag";
import {
  ToolApprovalPayload,
  approvalTitle,
} from "../../../components/tool-approval";
import { useAuth } from "../../../lib/auth/auth-context";
import {
  formatDateTime,
  formatDateTimeFull,
  VN_TIME,
} from "../../../lib/dates";
import { errorMessage, regionFailure } from "../../../lib/error-message";
import { apiClient } from "../../../lib/session";

type Reason = NonNullable<ApprovalViewOutcome["unavailable_reason"]>;

/** Why no code is offered, in words; one sentence per reason the server gives. */
const REASON: Record<Reason, string> = {
  not_pending: "Yêu cầu này đã được quyết hoặc đã hủy.",
  cannot_decide: "Bạn không có quyền quyết yêu cầu này.",
  requester: "Bạn đã tạo yêu cầu này nên không tự quyết được (tách nhiệm).",
  web_only: "Loại yêu cầu này chỉ quyết trên web.",
  not_linked:
    "Bạn chưa kết nối Zalo, nên chưa quyết qua Zalo được. Kết nối ở trang Cài đặt cá nhân.",
  comment_required: "Loại yêu cầu này cần nhận xét trước khi lấy mã.",
  channel_off: "Quyết qua Zalo chưa bật ở hệ thống này.",
};

const BREADCRUMB = [{ title: <Link href="/approvals">Duyệt</Link> }];

type Load =
  | { kind: "loading" }
  | { kind: "error"; failure: RegionFailure }
  | { kind: "ready"; approval: Approval; view: ApprovalViewOutcome };

/**
 * One approval, the page a notification and its Zalo message link to
 * (`/approvals/<id>?workspace=<id>`, ADR 0014). Opening it records a view;
 * the viewer may ask for a single-use code to decide it on Zalo, bound to the
 * comment written here. The decision itself is the server's: this page offers
 * a code only when the server says one can be issued, and says why not
 * otherwise.
 */
export default function ApprovalPage() {
  // `useSearchParams` needs a Suspense boundary above the component calling it.
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 6 }} />}>
      <ApprovalDetail />
    </Suspense>
  );
}

function ApprovalDetail() {
  const { id } = useParams<{ id: string }>();
  const wanted = useSearchParams().get("workspace");
  const { active, memberships, selectWorkspace } = useAuth();
  const member =
    wanted == null || memberships.some((m) => m.workspaceId === wanted);
  const switching = wanted != null && member && active?.workspaceId !== wanted;

  // The link names the approval's workspace; the API answers only within the
  // active one, so switch to it when the viewer belongs to it.
  useEffect(() => {
    if (switching && wanted) selectWorkspace(wanted);
  }, [switching, wanted, selectWorkspace]);

  const [state, setState] = useState<Load>({ kind: "loading" });
  const workspaceId = active?.workspaceId ?? null;

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const client = apiClient();
      const approval = await client.getApproval(id);
      // Opening is a view the server records, whatever is offered after it.
      const view = await client.viewApproval(id, {});
      setState({ kind: "ready", approval, view });
    } catch (error) {
      setState({ kind: "error", failure: regionFailure(error) });
    }
  }, [id]);

  useEffect(() => {
    if (switching || !member || workspaceId == null) return;
    void load();
  }, [load, switching, member, workspaceId]);

  if (!member) {
    return (
      <div className="mx-auto max-w-3xl">
        <PageHeader breadcrumb={BREADCRUMB} title="Yêu cầu duyệt" />
        <RegionState
          failure={{
            code: "not_found",
            message: "Yêu cầu không thuộc workspace nào của bạn.",
            requestId: null,
          }}
          what="yêu cầu"
        />
      </div>
    );
  }

  if (state.kind !== "ready") {
    return (
      <div className="mx-auto max-w-3xl">
        <PageHeader breadcrumb={BREADCRUMB} title="Yêu cầu duyệt" />
        <RegionState
          loading={state.kind === "loading"}
          failure={state.kind === "error" ? state.failure : null}
          what="yêu cầu"
          onRetry={() => void load()}
        />
      </div>
    );
  }

  const { approval, view } = state;
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        breadcrumb={[
          ...BREADCRUMB,
          { title: approvalTitle(approval.approval_type) },
        ]}
        title={approvalTitle(approval.approval_type)}
        tags={<ApprovalStatusTag status={approval.status} />}
      />
      <Flex vertical gap="middle">
        <Card size="small" title="Đang duyệt gì">
          <Flex vertical gap="small">
            <Typography.Text>{approval.reason}</Typography.Text>
            <Descriptions
              size="small"
              column={1}
              items={[
                {
                  key: "type",
                  label: "Loại",
                  children: (
                    <Typography.Text code className="break-all">
                      {approval.approval_type}
                    </Typography.Text>
                  ),
                },
                {
                  key: "created",
                  label: `Tạo lúc (${VN_TIME})`,
                  children: formatDateTimeFull(approval.created_at),
                },
                {
                  key: "viewed",
                  label: `Bạn xem lúc (${VN_TIME})`,
                  children: formatDateTimeFull(view.viewed_at),
                },
              ]}
            />
            <ToolApprovalPayload payload={approval.payload} />
          </Flex>
        </Card>
        <ZaloDecision approval={approval} view={view} />
        <Typography.Text>
          Quyết trên web: <Link href="/approvals">mở trang Duyệt</Link>.
        </Typography.Text>
      </Flex>
    </div>
  );
}

function ZaloDecision({
  approval,
  view,
}: {
  approval: Approval;
  view: ApprovalViewOutcome;
}) {
  const [comment, setComment] = useState("");
  const [issued, setIssued] = useState<ApprovalViewOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  // A code lives ten minutes; the page says so when it lapses rather than
  // keep showing digits the bot will refuse.
  useEffect(() => {
    setExpired(false);
    if (!issued?.expires_at) return;
    const left = Date.parse(issued.expires_at) - Date.now();
    // setTimeout holds at most 2^31-1 ms; a longer wait fires at once.
    const timer = setTimeout(
      () => setExpired(true),
      Math.min(Math.max(0, left), 2_147_483_647),
    );
    return () => clearTimeout(timer);
  }, [issued]);

  const reason = issued?.unavailable_reason ?? view.unavailable_reason;
  if (reason && reason !== "comment_required") {
    return (
      <Card size="small" title="Quyết qua Zalo">
        <Flex vertical gap="small">
          <Typography.Text>{REASON[reason]}</Typography.Text>
          {reason === "not_linked" && (
            <Link href="/settings">Mở trang Cài đặt cá nhân</Link>
          )}
        </Flex>
      </Card>
    );
  }

  const needsComment = view.requires_comment && !comment.trim();
  async function issue() {
    setBusy(true);
    try {
      const outcome = await apiClient().viewApproval(approval.id, {
        comment,
        issue_code: true,
      });
      setIssued(outcome);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const button = (
    <Button
      type="primary"
      size="large"
      icon={<MessageOutlined aria-hidden />}
      loading={busy}
      disabled={needsComment}
      onClick={() => void issue()}
    >
      {issued?.code ? "Lấy mã mới" : "Lấy mã để quyết qua Zalo"}
    </Button>
  );
  const commentRequired = REASON.comment_required;

  return (
    <Card size="small" title="Quyết qua Zalo">
      <Flex vertical gap="middle">
        <Typography.Text>
          Nhập nhận xét rồi lấy mã; gửi đúng câu lệnh cho bot Zalo. Mã dùng một
          lần, sống 10 phút, và không bao giờ được gửi qua Zalo.
        </Typography.Text>
        <Flex vertical gap={4}>
          <label htmlFor="approval-comment">
            <Typography.Text strong>
              {view.requires_comment
                ? "Nhận xét (bắt buộc)"
                : "Nhận xét (không bắt buộc)"}
            </Typography.Text>
          </label>
          <Input.TextArea
            id="approval-comment"
            rows={3}
            maxLength={2000}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Ví dụ: Mẫu đạt, đồng ý phát triển."
          />
        </Flex>
        <Flex wrap gap="small" align="center">
          {needsComment ? (
            <Tooltip title={commentRequired}>
              <span className="inline-flex">{button}</span>
            </Tooltip>
          ) : (
            button
          )}
          {needsComment && <Typography.Text>{commentRequired}</Typography.Text>}
        </Flex>
        {error != null && <Alert type="error" showIcon title={error} />}
        {issued?.code && issued.command_approve && issued.command_reject && (
          <Flex vertical gap="small" role="status">
            {expired ? (
              <Alert
                type="warning"
                showIcon
                title="Mã đã hết hạn. Bấm “Lấy mã mới” để quyết qua Zalo."
              />
            ) : (
              <>
                <Typography.Text>
                  Mã của bạn:{" "}
                  <Typography.Text strong code>
                    {issued.code}
                  </Typography.Text>{" "}
                  — hết hạn lúc {formatDateTime(issued.expires_at)} ({VN_TIME}).
                </Typography.Text>
                <Typography.Text>Để duyệt, gửi cho bot:</Typography.Text>
                <Typography.Text
                  code
                  copyable={{ text: issued.command_approve }}
                  className="break-all"
                >
                  {issued.command_approve}
                </Typography.Text>
                <Typography.Text>
                  Để không duyệt, gửi kèm lý do:
                </Typography.Text>
                <Typography.Text
                  code
                  copyable={{ text: issued.command_reject }}
                  className="break-all"
                >
                  {issued.command_reject}
                </Typography.Text>
              </>
            )}
          </Flex>
        )}
      </Flex>
    </Card>
  );
}
