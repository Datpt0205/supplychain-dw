"use client";

import { useCallback, useState, type ReactNode } from "react";
import { Tooltip, Typography } from "antd";
import { BadgeCheck, CircleX, ClipboardCheck, RefreshCw } from "lucide-react";
import type { Approval } from "@dw/contracts";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@dw/ui";
import { EmptyState } from "../../components/empty-state";
import { LoadMore } from "../../components/load-more";
import { PageHeading } from "../../components/page-heading";
import {
  ToolApprovalPayload,
  approvalTitle,
} from "../../components/tool-approval";
import { approvalClient } from "../../lib/approvals/registry";
import { useAuth } from "../../lib/auth/auth-context";
import { formatDateTime } from "../../lib/dates";
import { apiClient } from "../../lib/session";
import { useCachedPages } from "../../lib/use-cached-pages";

const STATUS_BADGE: Record<
  Approval["status"],
  {
    label: string;
    variant: "warning" | "success" | "destructive" | "secondary";
  }
> = {
  pending: { label: "Pending", variant: "warning" },
  approved: { label: "Approved", variant: "success" },
  rejected: { label: "Rejected", variant: "destructive" },
  cancelled: { label: "Cancelled", variant: "secondary" },
};

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
   * The scope stamped on this approval that the viewer lacks (ADR 0020), or
   * null. Read from the approval itself and the session, never inferred from
   * `approval_type`; the server refuses the decision either way.
   */
  function missingScope(approval: Approval): string | null {
    const scope = approval.required_scope;
    return scope !== null && !hasScope(scope) ? scope : null;
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
      setError(e instanceof Error ? e.message : "unknown error");
    } finally {
      setBusyId(null);
    }
  }

  const pending = approvals.filter((item) => item.status === "pending");
  const decided = approvals.filter((item) => item.status !== "pending");

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeading
        icon={ClipboardCheck}
        title="Approvals"
        description="A run that asks to change something outside this system pauses here until a person decides. Nothing on this page has happened yet."
        actions={
          <Button variant="outline" size="icon" onClick={reload}>
            <RefreshCw />
          </Button>
        }
      />
      {(error ?? loadError) != null && (
        <p className="text-sm text-destructive">
          {error ??
            (loadError instanceof Error ? loadError.message : "unknown error")}
        </p>
      )}
      {loading && loadError == null && <Skeleton className="h-64 w-full" />}
      {!loading && pending.length === 0 && (
        <EmptyState
          icon={ClipboardCheck}
          title="Nothing waiting for a decision"
          description="A request appears here the moment a worker reaches a side effect its policy will not let it perform alone."
        />
      )}

      {pending.map((approval) => {
        const lacking = missingScope(approval);
        const lockReason =
          lacking === null
            ? null
            : `Chỉ người có quyền ${lacking} được quyết yêu cầu này`;
        // Withdrawing your own request is not deciding it: the server lets the
        // requester reject without the stamped scope, so the page does too.
        const approveLocked = lacking !== null;
        const rejectLocked = lacking !== null && !approval.requested_by_me;
        // A disabled button takes no pointer events, so the tooltip hangs on a
        // wrapper; the same sentence also sits beside the buttons as text.
        const withLock = (locked: boolean, button: ReactNode) =>
          locked ? (
            <Tooltip title={lockReason}>
              <span className="inline-flex">{button}</span>
            </Tooltip>
          ) : (
            button
          );
        return (
          <Card key={approval.id} className="overflow-hidden">
            <CardHeader className="border-b bg-muted/40">
              <CardTitle className="flex flex-col items-start gap-3 text-base sm:flex-row sm:items-center sm:justify-between">
                <span>{approvalTitle(approval.approval_type)}</span>
                <Badge variant={STATUS_BADGE[approval.status].variant}>
                  {STATUS_BADGE[approval.status].label}
                </Badge>
              </CardTitle>
              <CardDescription>
                <span className="font-mono text-xs">
                  {approval.approval_type}
                </span>{" "}
                — {approval.reason}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4 pt-5 text-sm">
              <ToolApprovalPayload
                payload={approval.payload}
                className="bg-muted/50"
              />
              {!canDecide && (
                <p className="text-xs text-muted-foreground">
                  Your roles do not carry <strong>approvals.decide</strong>, so
                  this request is read-only for you.
                </p>
              )}
              {canDecide && (
                <div className="rounded-xl border bg-muted/30 p-4">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                    Your decision
                  </p>
                  <Input
                    value={comments[approval.id] ?? ""}
                    onChange={(event) =>
                      setComments((current) => ({
                        ...current,
                        [approval.id]: event.target.value,
                      }))
                    }
                    placeholder={
                      approval.requires_comment
                        ? "Note explaining the decision (required)"
                        : "Note explaining the decision (optional)"
                    }
                  />
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {withLock(
                      approveLocked,
                      <Button
                        onClick={() => void decide(approval, true)}
                        disabled={
                          approveLocked ||
                          busyId === approval.id ||
                          missingComment(approval)
                        }
                      >
                        <BadgeCheck />
                        {busyId === approval.id ? "Working…" : "Approve"}
                      </Button>,
                    )}
                    {withLock(
                      rejectLocked,
                      <Button
                        variant="destructive"
                        onClick={() => void decide(approval, false)}
                        disabled={
                          rejectLocked ||
                          busyId === approval.id ||
                          missingComment(approval)
                        }
                      >
                        <CircleX /> Reject
                      </Button>,
                    )}
                    {lockReason !== null && (
                      <Typography.Text type="secondary">
                        {lockReason}
                        {approval.requested_by_me &&
                          ". Bạn vẫn rút được yêu cầu của mình."}
                      </Typography.Text>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}

      {decided.length > 0 && (
        <Card className="overflow-hidden">
          <CardHeader>
            <CardTitle className="text-base">Recent decisions</CardTitle>
            <CardDescription>
              Already decided; kept here so a run can be traced back to the
              person who released it.
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-1">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Decided</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Run id</TableHead>
                  <TableHead>Outcome</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {decided.map((approval) => (
                  <TableRow key={approval.id} className="align-top">
                    <TableCell className="whitespace-nowrap font-mono text-xs">
                      {formatDateTime(approval.decided_at)}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {approval.approval_type}
                    </TableCell>
                    <TableCell className="max-w-64 truncate text-xs text-muted-foreground">
                      {approval.reason}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {approval.run_id ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_BADGE[approval.status].variant}>
                        {STATUS_BADGE[approval.status].label}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {!loading && pending.length > 0 && (
        <LoadMore
          hasMore={hasMore}
          loading={loadingMore}
          onLoadMore={loadMore}
          shown={approvals.length}
          noun="requests"
        />
      )}
    </div>
  );
}
