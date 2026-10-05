"use client";

import { useCallback } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@dw/ui";
import { EmptyState } from "../../../components/empty-state";
import { PageHeading } from "../../../components/page-heading";
import { CaseStateBadge } from "../../../components/supply-chain/case-state-badge";
import { MissingUpdateBadge } from "../../../components/supply-chain/missing-update-badge";
import { SlaStatusBadge } from "../../../components/supply-chain/sla-status-badge";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

/**
 * The Attention Queue: every PO case with a deterministic signal — SLA
 * breach, a supplier gone quiet — gathered in one place. Nothing here is
 * invented or ranked by a model; `GET /attention-queue` computes both
 * signals the same way the Case Workspace's own health cards do, just for
 * every active case in the tenant instead of one.
 */
export default function AttentionQueuePage() {
  const {
    data: items,
    loading,
    error,
  } = useCachedResource(
    "supply-chain:attention-queue",
    useCallback(() => apiClient().listAttentionQueue(), []),
  );

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeading
        icon={AlertTriangle}
        title="Cần chú ý"
        description="Case trễ SLA hoặc nhà cung cấp im lặng quá lâu — tính toán thuần túy, không suy diễn."
      />
      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : error != null || !items ? (
        // Never "every case is on track" read off a failed request — that is
        // the one claim this page exists to make, and a 403 did not make it.
        <p className="text-sm text-destructive">
          Không tải được danh sách cần chú ý:{" "}
          {error instanceof Error ? error.message : "lỗi không xác định"}
        </p>
      ) : items.length === 0 ? (
        <EmptyState
          icon={AlertTriangle}
          title="Không có case nào cần chú ý"
          description="Không case nào vượt mốc SLA đã được xác nhận, và case nào cũng có cập nhật gần đây từ nhà cung cấp. Mốc SLA còn chờ xác nhận không được tính."
        />
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={item.case.id}>
              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
                  <CardTitle className="text-sm font-medium">
                    <Link
                      href={`/supply-chain/po-cases/${item.case.id}`}
                      className="hover:underline"
                    >
                      {item.case.po_reference}
                    </Link>
                    <span className="ml-2 font-normal text-muted-foreground">
                      {item.case.supplier_name}
                    </span>
                  </CardTitle>
                  <CaseStateBadge state={item.case.state} />
                </CardHeader>
                <CardContent className="flex flex-wrap gap-2">
                  {item.sla && <SlaStatusBadge status={item.sla.status} />}
                  {item.missing_update && (
                    <MissingUpdateBadge status={item.missing_update.status} />
                  )}
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
