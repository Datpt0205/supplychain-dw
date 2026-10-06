"use client";

import { useCallback, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Button, Card, Empty, Flex, List, Timeline, Typography } from "antd";
import { StatusTag, PageHeader, RegionState } from "@dw/ui";
import { CaseDocumentsCard } from "../../../../components/supply-chain/case-documents-card";
import {
  CASE_STATE_LABEL,
  CASE_STATE_META,
  CaseStateTag,
  stepLabel,
} from "../../../../components/supply-chain/case-state-badge";
import {
  CaseSummary,
  type SummaryCell,
} from "../../../../components/supply-chain/case-summary";
import { supplyChainCrumbs } from "../../../../components/supply-chain/crumbs";
import {
  MissingUpdateTag,
  missingUpdateLabel,
} from "../../../../components/supply-chain/missing-update-badge";
import { OriginTag } from "../../../../components/supply-chain/origin-tag";
import {
  SlaStatusTag,
  milestoneLabel,
  slaStatusLabel,
} from "../../../../components/supply-chain/sla-status-badge";
import { SupplierEventTag } from "../../../../components/supply-chain/supplier-event-badge";
import { useAuth } from "../../../../lib/auth/auth-context";
import { formatDateTimeFull } from "../../../../lib/dates";
import { regionFailure } from "../../../../lib/error-message";
import { apiClient } from "../../../../lib/session";
import { useCachedResource } from "../../../../lib/use-cached-resource";

/**
 * The Case Workspace: one PO case's state, SLA/missing-update health,
 * supplier updates and delay-impact analyses in one place.
 *
 * Each section is its own `useCachedResource` call rather than one combined
 * fetch, so a slow or failing section (e.g. no supplier updates yet) never
 * blocks the others from rendering — the case header shows as soon as the
 * case itself loads, and each region draws its own loading, empty and
 * failure.
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
  // approvals only: the same route never returns a decided one, so a case
  // whose action was already decided shows nothing here — its own state
  // already reflects that outcome. A tenant with more pending approvals
  // platform-wide than one page holds could miss one still further back;
  // worth a server-side filter if that becomes real.
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
  if (!poCase) {
    return (
      <div className="mx-auto max-w-6xl">
        <PageHeader
          breadcrumb={supplyChainCrumbs({
            title: "Hồ sơ PO",
            href: "/supply-chain/po-cases",
          })}
          title="Hồ sơ PO"
        />
        <RegionState
          loading={caseResource.loading}
          failure={
            caseResource.error != null
              ? regionFailure(caseResource.error)
              : null
          }
          what="Hồ sơ PO"
          onRetry={caseResource.reload}
          rows={8}
        />
      </div>
    );
  }

  const step = CASE_STATE_META[poCase.state].step;
  const sla = slaResource.data;
  const missing = missingUpdateResource.data;
  const approvals = relatedApprovalsResource.data;
  const pendingCell = (loading: boolean, failed: boolean): ReactNode | null =>
    loading ? "Đang tải…" : failed ? "Không tải được" : null;

  const cells: SummaryCell[] = [
    {
      key: "step",
      label: "Bước",
      value: stepLabel(step) ?? CASE_STATE_LABEL[poCase.state],
      sub: poCase.interrupted_state
        ? `Tạm dừng tại ${CASE_STATE_LABEL[poCase.interrupted_state]}`
        : undefined,
    },
    {
      key: "sla",
      label: "SLA",
      value:
        pendingCell(slaResource.loading, slaResource.error != null) ??
        (sla ? slaStatusLabel(sla.status) : "—"),
      sub: sla
        ? `${sla.milestone ? `Mốc ${milestoneLabel(sla.milestone)} · ` : ""}${sla.age_days} ngày${sla.threshold_days !== null ? ` / hạn ${sla.threshold_days} ngày` : ""}`
        : undefined,
      tone: sla?.status === "breached" ? "err" : undefined,
    },
    {
      key: "supplier",
      label: "Cập nhật của NCC",
      value:
        pendingCell(
          missingUpdateResource.loading,
          missingUpdateResource.error != null,
        ) ?? (missing ? missingUpdateLabel(missing.status) : "—"),
      sub: missing ? `Im lặng ${missing.age_days} ngày` : undefined,
      tone:
        missing?.status === "escalation_due"
          ? "err"
          : missing?.status === "reminder_due"
            ? "warn"
            : undefined,
    },
    {
      key: "approvals",
      label: "Chờ duyệt",
      value:
        pendingCell(
          relatedApprovalsResource.loading,
          relatedApprovalsResource.error != null,
        ) ??
        (approvals && approvals.length > 0
          ? `${approvals.length} yêu cầu`
          : "Không có"),
      sub:
        approvals && approvals.length > 0
          ? "Bước tiếp theo chờ một người quyết"
          : undefined,
      tone: approvals && approvals.length > 0 ? "warn" : undefined,
    },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs(
          { title: "Hồ sơ PO", href: "/supply-chain/po-cases" },
          poCase.po_reference,
        )}
        meta={
          <Typography.Text type="secondary">
            Hồ sơ PO · NCC {poCase.supplier_name}
          </Typography.Text>
        }
        title={poCase.po_reference}
        tags={<CaseStateTag state={poCase.state} />}
        description={`Tạo lúc ${formatDateTimeFull(poCase.created_at)} · phiên bản ${poCase.version}`}
      />
      <Flex vertical gap="middle">
        <CaseSummary label="Tóm tắt Hồ sơ PO" cells={cells} />

        {sla && (
          <Flex wrap gap="small" align="center">
            <SlaStatusTag status={sla.status} />
            {missing && <MissingUpdateTag status={missing.status} />}
            {missing && (
              <Typography.Text type="secondary">
                NCC im lặng từ {formatDateTimeFull(missing.reference_at)}
              </Typography.Text>
            )}
          </Flex>
        )}

        <Card title="Đang chờ duyệt">
          {relatedApprovalsResource.loading ||
          relatedApprovalsResource.error != null ? (
            <RegionState
              compact
              loading={relatedApprovalsResource.loading}
              failure={
                relatedApprovalsResource.error != null
                  ? regionFailure(relatedApprovalsResource.error)
                  : null
              }
              what="yêu cầu đang chờ duyệt"
              onRetry={relatedApprovalsResource.reload}
              rows={1}
            />
          ) : (
            <List
              dataSource={approvals ?? []}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="Không có yêu cầu nào đang chờ duyệt cho hồ sơ này."
                  />
                ),
              }}
              renderItem={(approval) => (
                <List.Item
                  key={approval.id}
                  extra={
                    <Link href="/approvals">
                      <Button>Mở trang Duyệt</Button>
                    </Link>
                  }
                >
                  <List.Item.Meta
                    title={
                      typeof approval.payload.action === "string"
                        ? approval.payload.action
                        : approval.approval_type
                    }
                    description={approval.reason}
                  />
                </List.Item>
              )}
            />
          )}
        </Card>

        <Card title="Lịch sử trạng thái">
          {transitionsResource.loading || transitionsResource.error != null ? (
            <RegionState
              compact
              loading={transitionsResource.loading}
              failure={
                transitionsResource.error != null
                  ? regionFailure(transitionsResource.error)
                  : null
              }
              what="lịch sử trạng thái"
              onRetry={transitionsResource.reload}
              rows={3}
            />
          ) : !transitionsResource.data?.length ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="Hồ sơ chưa chuyển trạng thái lần nào."
            />
          ) : (
            <Timeline
              items={transitionsResource.data.map((transition, index) => ({
                // A transition row has no id of its own; its position in the
                // (already server-ordered) timeline is stable.
                key: index,
                content: (
                  <Flex vertical gap={2}>
                    <Flex wrap gap="small" align="center">
                      <CaseStateTag state={transition.from_state} />
                      <span aria-hidden>→</span>
                      <CaseStateTag state={transition.to_state} />
                    </Flex>
                    <Typography.Text type="secondary">
                      {formatDateTimeFull(transition.occurred_at)}
                      {transition.reason && ` · ${transition.reason}`}
                    </Typography.Text>
                  </Flex>
                ),
              }))}
            />
          )}
        </Card>

        <Card title="Cập nhật của NCC">
          {supplierUpdatesResource.loading ||
          supplierUpdatesResource.error != null ? (
            <RegionState
              compact
              loading={supplierUpdatesResource.loading}
              failure={
                supplierUpdatesResource.error != null
                  ? regionFailure(supplierUpdatesResource.error)
                  : null
              }
              what="cập nhật của NCC"
              onRetry={supplierUpdatesResource.reload}
              rows={3}
            />
          ) : (
            <List
              dataSource={supplierUpdatesResource.data ?? []}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="Chưa có cập nhật nào của NCC."
                  />
                ),
              }}
              renderItem={(update) => (
                <List.Item key={update.id}>
                  <Flex vertical gap="small" className="w-full">
                    <Flex wrap gap="small" align="center">
                      <SupplierEventTag type={update.event_type} />
                      <OriginTag origin="model_read" />
                      {update.requires_confirmation && (
                        <StatusTag tone="warn">Cần người xác nhận</StatusTag>
                      )}
                      {update.delay_days !== null && (
                        <Typography.Text>
                          Trễ {update.delay_days} ngày
                        </Typography.Text>
                      )}
                      <Typography.Text type="secondary">
                        {formatDateTimeFull(update.created_at)}
                      </Typography.Text>
                    </Flex>
                    {/* What the supplier sent, as sent: the source of what
                        the model read (ui-quality §7). */}
                    <Typography.Paragraph
                      className="whitespace-pre-wrap"
                      style={{ margin: 0 }}
                    >
                      {update.raw_text}
                    </Typography.Paragraph>
                    {update.source_ref && (
                      <Typography.Text>
                        <Typography.Text strong>Trích dẫn:</Typography.Text> “
                        {update.source_ref}”
                      </Typography.Text>
                    )}
                    {update.reason && (
                      <Typography.Text>
                        <Typography.Text strong>Lý do:</Typography.Text>{" "}
                        {update.reason}
                      </Typography.Text>
                    )}
                  </Flex>
                </List.Item>
              )}
            />
          )}
        </Card>

        <Card title="Phân tích ảnh hưởng trễ tiến độ">
          {delayImpactResource.loading || delayImpactResource.error != null ? (
            <RegionState
              compact
              loading={delayImpactResource.loading}
              failure={
                delayImpactResource.error != null
                  ? regionFailure(delayImpactResource.error)
                  : null
              }
              what="phân tích ảnh hưởng trễ"
              onRetry={delayImpactResource.reload}
              rows={3}
            />
          ) : (
            <List
              dataSource={delayImpactResource.data ?? []}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="Chưa có phân tích ảnh hưởng nào."
                  />
                ),
              }}
              renderItem={(analysis) => (
                <List.Item key={analysis.id}>
                  <Flex vertical gap="small" className="w-full">
                    <Flex wrap gap="small" align="center">
                      <Typography.Text strong>
                        Trễ {analysis.delay_days} ngày
                      </Typography.Text>
                      <OriginTag origin="model_written" />
                      <Typography.Text type="secondary">
                        {formatDateTimeFull(analysis.created_at)}
                      </Typography.Text>
                    </Flex>
                    {analysis.impacted_milestones.length > 0 && (
                      <Flex wrap gap="small">
                        {analysis.impacted_milestones.map((impacted) => (
                          <StatusTag key={impacted.milestone} tone="outline">
                            {milestoneLabel(impacted.milestone)}: +
                            {impacted.estimated_delay_days} ngày
                          </StatusTag>
                        ))}
                      </Flex>
                    )}
                    {analysis.assumptions.length > 0 && (
                      <ul className="m-0 list-disc ps-5">
                        {analysis.assumptions.map((assumption, index) => (
                          // Assumptions are model output with no id of their
                          // own — position is stable within one analysis.
                          // eslint-disable-next-line react/no-array-index-key
                          <li key={index}>
                            <Typography.Text>{assumption}</Typography.Text>
                          </li>
                        ))}
                      </ul>
                    )}
                    {analysis.mitigation_options.map((option, index) => (
                      <Typography.Text
                        // eslint-disable-next-line react/no-array-index-key
                        key={index}
                      >
                        <Typography.Text strong>
                          {option.description}
                        </Typography.Text>{" "}
                        <Typography.Text type="secondary">
                          — {option.tradeoff}
                        </Typography.Text>
                      </Typography.Text>
                    ))}
                  </Flex>
                </List.Item>
              )}
            />
          )}
        </Card>

        <CaseDocumentsCard
          caseKind="po"
          caseId={id}
          canUpload={hasScope("supply_chain.document.write")}
        />
      </Flex>
    </div>
  );
}
