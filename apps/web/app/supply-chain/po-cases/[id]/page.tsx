"use client";

import { useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  ClipboardCheck,
  History,
  Layers,
  ListChecks,
  PackageSearch,
  Timer,
  TrendingDown,
} from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
} from "@dw/ui";
import { EmptyState } from "../../../../components/empty-state";
import { CaseDocumentsCard } from "../../../../components/supply-chain/case-documents-card";
import { PageHeading } from "../../../../components/page-heading";
import { CaseStateBadge } from "../../../../components/supply-chain/case-state-badge";
import { MissingUpdateBadge } from "../../../../components/supply-chain/missing-update-badge";
import { SlaStatusBadge } from "../../../../components/supply-chain/sla-status-badge";
import { SupplierEventBadge } from "../../../../components/supply-chain/supplier-event-badge";
import { useAuth } from "../../../../lib/auth/auth-context";
import { formatDateTime } from "../../../../lib/dates";
import { apiClient } from "../../../../lib/session";
import { useCachedResource } from "../../../../lib/use-cached-resource";

/**
 * The Case Workspace: one PO case's state, SLA/missing-update health,
 * supplier updates and delay-impact analyses in one place.
 *
 * Each section is its own `useCachedResource` call rather than one combined
 * fetch, so a slow or failing section (e.g. no supplier updates yet) never
 * blocks the others from rendering — the case header shows as soon as the
 * case itself loads. `POCase` is the first, and so far only, case type this
 * shell renders; a second one would extract the layout below into something
 * genuinely generic, which one consumer does not yet justify.
 */
export default function POCaseWorkspacePage() {
  const { id } = useParams<{ id: string }>();
  const { hasScope } = useAuth();

  const caseResource = useCachedResource(
    `supply-chain:po-case:${id}`,
    useCallback(() => apiClient().getPOCase(id), [id]),
  );
  const slaResource = useCachedResource(
    `supply-chain:po-case:${id}:sla`,
    useCallback(() => apiClient().getSLAEvaluation(id), [id]),
  );
  const missingUpdateResource = useCachedResource(
    `supply-chain:po-case:${id}:missing-update`,
    useCallback(() => apiClient().getMissingUpdateStatus(id), [id]),
  );
  // GET /api/v1/approvals has no case filter of its own — it is a platform
  // route, generic across every context's approval types, with no reason to
  // know what `po_case_id` means. Filtered here instead, against pending
  // approvals only: the same route never returns a decided one (its own
  // query is `WHERE status = 'pending'`), so a case whose action was already
  // approved or rejected shows nothing here — which is correct, since the
  // case's own state already reflects that outcome. A tenant with more
  // pending approvals platform-wide than one page holds could miss one
  // still further back; worth a server-side filter if that becomes real.
  const relatedApprovalsResource = useCachedResource(
    `supply-chain:po-case:${id}:approvals`,
    useCallback(async () => {
      const page = await apiClient().listApprovals({ limit: 200 });
      return page.items.filter(
        (approval) => approval.payload.po_case_id === id,
      );
    }, [id]),
  );
  const transitionsResource = useCachedResource(
    `supply-chain:po-case:${id}:transitions`,
    useCallback(() => apiClient().listCaseTransitions(id), [id]),
  );
  const supplierUpdatesResource = useCachedResource(
    `supply-chain:po-case:${id}:supplier-updates`,
    useCallback(() => apiClient().listSupplierUpdates(id), [id]),
  );
  const delayImpactResource = useCachedResource(
    `supply-chain:po-case:${id}:delay-impact`,
    useCallback(() => apiClient().listDelayImpactAnalyses(id), [id]),
  );

  const poCase = caseResource.data;

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <Link
        href="/supply-chain/po-cases"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Danh sách PO case
      </Link>

      {caseResource.loading ? (
        <Skeleton className="h-32 w-full" />
      ) : poCase === null ? (
        <EmptyState
          icon={PackageSearch}
          title="Không tìm thấy case"
          description="Case này không tồn tại, hoặc bạn không có quyền xem."
        />
      ) : (
        <>
          <PageHeading
            icon={PackageSearch}
            eyebrow={poCase.supplier_name}
            title={poCase.po_reference}
            description={`Tạo lúc ${formatDateTime(poCase.created_at)} · phiên bản ${poCase.version}`}
            actions={
              <div className="flex flex-wrap gap-1.5">
                <CaseStateBadge state={poCase.state} />
                {poCase.interrupted_state && (
                  <Badge variant="outline">
                    tạm dừng tại {poCase.interrupted_state}
                  </Badge>
                )}
              </div>
            }
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Card>
              <CardHeader className="flex flex-row items-center gap-2 space-y-0">
                <Timer className="size-4 text-muted-foreground" />
                <CardTitle className="text-sm">SLA</CardTitle>
              </CardHeader>
              <CardContent>
                {slaResource.loading ? (
                  <Skeleton className="h-10 w-full" />
                ) : slaResource.data ? (
                  <div className="space-y-1.5">
                    <SlaStatusBadge status={slaResource.data.status} />
                    <p className="text-sm text-muted-foreground">
                      {slaResource.data.milestone
                        ? `Mốc: ${slaResource.data.milestone} · `
                        : ""}
                      {slaResource.data.age_days} ngày
                      {slaResource.data.threshold_days !== null &&
                        ` / hạn ${slaResource.data.threshold_days} ngày`}
                    </p>
                  </div>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center gap-2 space-y-0">
                <ListChecks className="size-4 text-muted-foreground" />
                <CardTitle className="text-sm">
                  Cập nhật từ nhà cung cấp
                </CardTitle>
              </CardHeader>
              <CardContent>
                {missingUpdateResource.loading ? (
                  <Skeleton className="h-10 w-full" />
                ) : missingUpdateResource.data ? (
                  <div className="space-y-1.5">
                    <MissingUpdateBadge
                      status={missingUpdateResource.data.status}
                    />
                    <p className="text-sm text-muted-foreground">
                      Im lặng {missingUpdateResource.data.age_days} ngày, từ{" "}
                      {formatDateTime(missingUpdateResource.data.reference_at)}
                    </p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <ClipboardCheck className="size-4 text-muted-foreground" />
                Đang chờ duyệt
              </CardTitle>
              <CardDescription>
                Hành động trên case này đang tạm dừng chờ một người quyết định.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {relatedApprovalsResource.loading ? (
                <Skeleton className="h-10 w-full" />
              ) : !relatedApprovalsResource.data ||
                relatedApprovalsResource.data.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Không có yêu cầu nào đang chờ duyệt cho case này.
                </p>
              ) : (
                <ul className="space-y-2">
                  {relatedApprovalsResource.data.map((approval) => (
                    <li
                      key={approval.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-xl border bg-card px-4 py-3"
                    >
                      <div>
                        <p className="text-sm font-medium">
                          {typeof approval.payload.action === "string"
                            ? approval.payload.action
                            : approval.approval_type}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {approval.reason}
                        </p>
                      </div>
                      <Button asChild variant="outline" size="sm">
                        <Link href="/approvals">Xem trong Approvals</Link>
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <History className="size-4 text-muted-foreground" />
                Lịch sử trạng thái
              </CardTitle>
              <CardDescription>Từ lúc tạo đến hiện tại.</CardDescription>
            </CardHeader>
            <CardContent>
              {transitionsResource.loading ? (
                <Skeleton className="h-20 w-full" />
              ) : !transitionsResource.data ||
                transitionsResource.data.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Case chưa chuyển trạng thái lần nào.
                </p>
              ) : (
                <ol className="space-y-3">
                  {transitionsResource.data.map((transition, index) => (
                    // A transition row has no id of its own; its position in
                    // the (already server-ordered) timeline is stable.
                    // eslint-disable-next-line react/no-array-index-key
                    <li key={index} className="flex items-start gap-3">
                      <div className="mt-1 size-2 shrink-0 rounded-full bg-primary" />
                      <div className="min-w-0">
                        <p className="text-sm">
                          <CaseStateBadge state={transition.from_state} /> →{" "}
                          <CaseStateBadge state={transition.to_state} />
                        </p>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {formatDateTime(transition.occurred_at)}
                          {transition.reason && ` · ${transition.reason}`}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Layers className="size-4 text-muted-foreground" />
                Cập nhật nhà cung cấp
              </CardTitle>
              <CardDescription>Mới nhất trước.</CardDescription>
            </CardHeader>
            <CardContent>
              {supplierUpdatesResource.loading ? (
                <Skeleton className="h-20 w-full" />
              ) : !supplierUpdatesResource.data ||
                supplierUpdatesResource.data.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Chưa có cập nhật nào.
                </p>
              ) : (
                <ul className="space-y-3">
                  {supplierUpdatesResource.data.map((update) => (
                    <li
                      key={update.id}
                      className="rounded-xl border bg-card px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <SupplierEventBadge type={update.event_type} />
                          {update.requires_confirmation && (
                            <Badge variant="warning">Cần xác nhận</Badge>
                          )}
                          {update.delay_days !== null && (
                            <span className="text-xs text-muted-foreground">
                              trễ {update.delay_days} ngày
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {formatDateTime(update.created_at)}
                        </span>
                      </div>
                      <p className="mt-2 whitespace-pre-wrap text-sm">
                        {update.raw_text}
                      </p>
                      {update.reason && (
                        <p className="mt-1 text-sm text-muted-foreground">
                          <span className="font-medium text-foreground">
                            Lý do:
                          </span>{" "}
                          {update.reason}
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <TrendingDown className="size-4 text-muted-foreground" />
                Phân tích ảnh hưởng trễ tiến độ
              </CardTitle>
              <CardDescription>
                Mốc bị ảnh hưởng, giả định và phương án xử lý — mới nhất trước.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {delayImpactResource.loading ? (
                <Skeleton className="h-20 w-full" />
              ) : !delayImpactResource.data ||
                delayImpactResource.data.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Chưa có phân tích ảnh hưởng nào.
                </p>
              ) : (
                <ul className="space-y-4">
                  {delayImpactResource.data.map((analysis) => (
                    <li
                      key={analysis.id}
                      className="rounded-xl border bg-card px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium">
                          Trễ {analysis.delay_days} ngày
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {formatDateTime(analysis.created_at)}
                        </span>
                      </div>
                      {analysis.impacted_milestones.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {analysis.impacted_milestones.map((impacted) => (
                            <Badge key={impacted.milestone} variant="outline">
                              {impacted.milestone}: +
                              {impacted.estimated_delay_days}
                              ngày
                            </Badge>
                          ))}
                        </div>
                      )}
                      {analysis.assumptions.length > 0 && (
                        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-muted-foreground">
                          {analysis.assumptions.map((assumption, index) => (
                            // Assumptions are model output with no id of their
                            // own — position is stable within one analysis.
                            // eslint-disable-next-line react/no-array-index-key
                            <li key={index}>{assumption}</li>
                          ))}
                        </ul>
                      )}
                      {analysis.mitigation_options.length > 0 && (
                        <div className="mt-2 space-y-1">
                          {analysis.mitigation_options.map((option, index) => (
                            <p
                              // eslint-disable-next-line react/no-array-index-key
                              key={index}
                              className="text-sm"
                            >
                              <span className="font-medium">
                                {option.description}
                              </span>{" "}
                              <span className="text-muted-foreground">
                                — {option.tradeoff}
                              </span>
                            </p>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <CaseDocumentsCard
            caseKind="po"
            caseId={id}
            canUpload={hasScope("supply_chain.document.write")}
          />
        </>
      )}
    </div>
  );
}
