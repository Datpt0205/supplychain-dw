"use client";

import { useCallback, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  List,
  Select,
  Table,
  Timeline,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { StatusTag, PageHeader, RegionState } from "@dw/ui";
import { formatCount } from "../../../../lib/money";
import { LoadError } from "../../../../components/load-error";
import { LoadMore } from "../../../../components/load-more";
import {
  ApiError,
  type OrderKind,
  type POCaseDetail,
  type POCaseLine,
} from "@dw/api-client";
import { CaseDocumentsCard } from "../../../../components/supply-chain/case-documents-card";
import {
  CASE_STATE_LABEL,
  CASE_STATE_META,
  CaseStateTag,
  ORDER_KIND_LABEL,
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
import { PackagingDesignCard } from "../../../../components/supply-chain/packaging-design-card";
import { DraftsCard } from "../../../../components/supply-chain/drafts-card";
import { POCommercialCard } from "../../../../components/supply-chain/po-commercial-card";
import { poReferenceLabel } from "../../../../components/supply-chain/po-reference";
import {
  SlaStatusTag,
  milestoneLabel,
  slaStatusLabel,
} from "../../../../components/supply-chain/sla-status-badge";
import { SupplierEventTag } from "../../../../components/supply-chain/supplier-event-badge";
import { useAuth } from "../../../../lib/auth/auth-context";
import { formatDateTimeFull } from "../../../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../../../lib/directory";
import { errorMessage } from "../../../../lib/error-message";
import { useOnline } from "../../../../lib/hooks/use-online";
import { useAttemptKey } from "../../../../lib/idempotency-key";
import { apiClient } from "../../../../lib/session";
import { useCachedPages } from "../../../../lib/use-cached-pages";
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
  // The server filters by the case (the approval inbox narrowed by the case
  // id in the payload): `total` counts every pending one, however many the
  // tenant has, and `visible` is false when the caller may not read the inbox.
  const relatedApprovalsResource = useCachedResource(
    `supply-chain:po-case:${id}:approvals`,
    useCallback(() => apiClient().listPOCaseApprovals(id), [id]),
  );
  // Newest first, a page at a time: a long-running case's history has no
  // bound of its own.
  const transitionsResource = useCachedPages(
    `supply-chain:po-case:${id}:transitions`,
    useCallback(
      (cursor: string | null) =>
        apiClient().listCaseTransitions(id, { cursor: cursor ?? undefined }),
      [id],
    ),
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
        {caseResource.error != null ? (
          <LoadError error={caseResource.error} onRetry={caseResource.reload} />
        ) : caseResource.loading ? (
          <RegionState kind="loading" />
        ) : null}
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
        (approvals && !approvals.visible
          ? "Không xem được"
          : approvals && approvals.total > 0
            ? `${approvals.total} yêu cầu`
            : "Không có"),
      sub:
        approvals && !approvals.visible
          ? "Cần quyền xem trang Duyệt"
          : approvals && approvals.total > 0
            ? "Bước tiếp theo chờ một người quyết"
            : undefined,
      tone: approvals && approvals.total > 0 ? "warn" : undefined,
    },
  ];

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs(
          { title: "Hồ sơ PO", href: "/supply-chain/po-cases" },
          poReferenceLabel(poCase.po_reference),
        )}
        title={poReferenceLabel(poCase.po_reference)}
        tags={
          <>
            <Typography.Text type="secondary">
              Hồ sơ PO · NCC {poCase.supplier_name}
            </Typography.Text>
            <CaseStateTag state={poCase.state} />
          </>
        }
        subtitle={`Tạo lúc ${formatDateTimeFull(poCase.created_at)} · phiên bản ${poCase.version}`}
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

        {poCase.state === "order_requested" && (
          <CreatePOCard
            poCase={poCase}
            onCreated={() => {
              caseResource.reload();
              transitionsResource.reload();
            }}
          />
        )}

        <OrderCard poCase={poCase} />

        <Card title="Đang chờ duyệt">
          {relatedApprovalsResource.loading ||
          relatedApprovalsResource.error != null ? (
            <>
              {relatedApprovalsResource.error != null ? (
                <LoadError
                  error={relatedApprovalsResource.error}
                  onRetry={relatedApprovalsResource.reload}
                  compact
                />
              ) : relatedApprovalsResource.loading ? (
                <RegionState kind="loading" compact />
              ) : null}
            </>
          ) : (
            <List
              dataSource={approvals?.items ?? []}
              locale={{
                emptyText: (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={
                      approvals && !approvals.visible
                        ? "Bạn không có quyền xem trang Duyệt, nên yêu cầu chờ duyệt không hiện ở đây."
                        : "Không có yêu cầu nào đang chờ duyệt cho hồ sơ này."
                    }
                  />
                ),
              }}
              footer={
                approvals && approvals.total > approvals.items.length
                  ? `${approvals.items.length} yêu cầu mới nhất trong ${approvals.total}`
                  : undefined
              }
              renderItem={(approval) => (
                <List.Item
                  key={approval.id}
                  extra={
                    <Link href={`/approvals/${approval.id}`}>
                      <Button>Mở yêu cầu</Button>
                    </Link>
                  }
                >
                  <List.Item.Meta
                    title={approval.action}
                    description={
                      approval.requested_at
                        ? `Gửi lúc ${formatDateTimeFull(approval.requested_at)}`
                        : undefined
                    }
                  />
                </List.Item>
              )}
            />
          )}
        </Card>

        <Card title="Lịch sử trạng thái">
          {transitionsResource.loading || transitionsResource.error != null ? (
            <>
              {transitionsResource.error != null ? (
                <LoadError
                  error={transitionsResource.error}
                  onRetry={transitionsResource.reload}
                  compact
                />
              ) : transitionsResource.loading ? (
                <RegionState kind="loading" compact />
              ) : null}
            </>
          ) : transitionsResource.items.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="Hồ sơ chưa chuyển trạng thái lần nào."
            />
          ) : (
            <Flex vertical gap="middle">
              <Timeline
                items={transitionsResource.items.map((transition, index) => ({
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
              <LoadMore
                hasMore={transitionsResource.hasMore}
                loading={transitionsResource.loadingMore}
                onLoadMore={transitionsResource.loadMore}
                shown={transitionsResource.items.length}
                noun="lần chuyển"
              />
            </Flex>
          )}
        </Card>

        <Card title="Cập nhật của NCC">
          {supplierUpdatesResource.loading ||
          supplierUpdatesResource.error != null ? (
            <>
              {supplierUpdatesResource.error != null ? (
                <LoadError
                  error={supplierUpdatesResource.error}
                  onRetry={supplierUpdatesResource.reload}
                  compact
                />
              ) : supplierUpdatesResource.loading ? (
                <RegionState kind="loading" compact />
              ) : null}
            </>
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
            <>
              {delayImpactResource.error != null ? (
                <LoadError
                  error={delayImpactResource.error}
                  onRetry={delayImpactResource.reload}
                  compact
                />
              ) : delayImpactResource.loading ? (
                <RegionState kind="loading" compact />
              ) : null}
            </>
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

        <POCommercialCard caseId={id} />

        <DraftsCard caseKind="po" caseId={id} />

        <PackagingDesignCard
          caseId={id}
          onStep={() => {
            caseResource.reload();
          }}
        />

        <CaseDocumentsCard
          caseKind="po"
          caseId={id}
          canUpload={hasScope("supply_chain.document.write")}
        />
      </Flex>
    </div>
  );
}

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

// The constraint a PO number already used in the company is refused by: the
// server names it in a 409's details, and the page shows the sentence at the
// field.
const PO_REFERENCE_TAKEN = "uq_po_cases_tenant_id_po_reference";

const ORDER_KIND_OPTIONS = (Object.keys(ORDER_KIND_LABEL) as OrderKind[]).map(
  (value) => ({ value, label: ORDER_KIND_LABEL[value] }),
);

/** What the order is: its kind, Category, PIC, the product case it came from
 * and its lines (one per SKU), as the server holds them. */
function OrderCard({ poCase }: { poCase: POCaseDetail }) {
  const members = useWorkspaceMembers();
  const columns: TableColumnsType<POCaseLine> = [
    {
      title: "Mã SKU",
      dataIndex: "sku_code",
      render: (value: string | null) =>
        value === null ? "—" : <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: "Biến thể",
      dataIndex: "variant_label",
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "Số lượng",
      dataIndex: "quantity",
      align: "right",
      render: (value: number | null) =>
        value === null ? "Chưa có" : formatCount(value),
    },
  ];
  return (
    <Card title="Đơn hàng">
      <Flex vertical gap="middle">
        <Descriptions
          size="small"
          column={{ xs: 1, md: 2 }}
          items={[
            {
              key: "kind",
              label: "Loại đơn",
              children: ORDER_KIND_LABEL[poCase.order_kind],
            },
            {
              key: "category",
              label: "Category",
              children: poCase.category ?? "—",
            },
            {
              key: "pic",
              label: "PIC",
              children: memberName(members, poCase.pic_user_id) ?? "—",
            },
            {
              key: "source",
              label: "Nguồn",
              children: poCase.product_dev_case_id ? (
                <Link
                  href={`/supply-chain/product-cases/${poCase.product_dev_case_id}`}
                >
                  Hồ sơ phát triển sản phẩm
                </Link>
              ) : (
                "Mở trực tiếp, không qua giai đoạn 1"
              ),
            },
          ]}
        />
        <Table<POCaseLine>
          rowKey="sku_id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={poCase.lines}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="Hồ sơ không có dòng hàng nào."
              />
            ),
          }}
        />
      </Flex>
    </Card>
  );
}

interface CreatePOValues {
  poReference: string;
  orderKind: OrderKind;
  quantities: Record<string, number | null>;
}

/**
 * Step 10 on a case ĐẶT HÀNG opened (ADR 0017): the PO's number, its kind and
 * the quantity of each line, prefilled from the line. Whether the number is
 * free in the company, and who may create the PO (a duty each company sets,
 * not sent with the case), are the server's answers: a taken number is shown
 * at its field, any other refusal with its sentence.
 */
function CreatePOCard({
  poCase,
  onCreated,
}: {
  poCase: POCaseDetail;
  onCreated: () => void;
}) {
  const [form] = Form.useForm<CreatePOValues>();
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const [submitting, setSubmitting] = useState(false);
  const [invalid, setInvalid] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const submit = async (values: CreatePOValues) => {
    setInvalid(0);
    setError(null);
    const input = {
      poReference: values.poReference,
      orderKind: values.orderKind,
      // Every line, required by the form: a quantity set here or corrected.
      lines: poCase.lines.map((line) => ({
        skuId: line.sku_id,
        quantity: values.quantities[line.sku_id] as number,
      })),
    };
    setSubmitting(true);
    try {
      const created = await apiClient().createPO(
        poCase.id,
        input,
        attemptKey(input),
      );
      void message.success(
        `Đã tạo PO ${poReferenceLabel(created.po_reference)}.`,
      );
      onCreated();
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status === 409 &&
        caught.body.details.constraint === PO_REFERENCE_TAKEN
      ) {
        form.setFields([
          { name: "poReference", errors: [caught.body.message] },
        ]);
      } else {
        setError(errorMessage(caught));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card title="Tạo PO (bước 10)">
      <Form<CreatePOValues>
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        scrollToFirstError={{ focus: true }}
        initialValues={{
          orderKind: poCase.product_dev_case_id ? "new" : poCase.order_kind,
          quantities: Object.fromEntries(
            poCase.lines.map((line) => [line.sku_id, line.quantity]),
          ),
        }}
        onFinish={(values) => void submit(values)}
        onFinishFailed={({ errorFields }) => setInvalid(errorFields.length)}
      >
        <Typography.Paragraph>
          ĐẶT HÀNG đã mở hồ sơ này; nhập số PO đã tạo, loại đơn và số lượng từng
          SKU để hồ sơ sang {CASE_STATE_LABEL.po_created}.
        </Typography.Paragraph>
        {invalid > 0 && (
          <Alert
            type="error"
            showIcon
            title={`Còn ${invalid} trường cần sửa`}
          />
        )}
        <Form.Item
          name="poReference"
          label="Số PO"
          rules={[
            { required: true, whitespace: true, message: "Nhập số PO" },
            { max: 200, message: "Số PO tối đa 200 ký tự" },
          ]}
        >
          <Input autoComplete="off" placeholder="Ví dụ: PO-2026-007" />
        </Form.Item>
        <Form.Item
          name="orderKind"
          label="Loại đơn"
          rules={[{ required: true, message: "Chọn loại đơn" }]}
        >
          <Select
            aria-label="Loại đơn"
            options={ORDER_KIND_OPTIONS}
            virtual={false}
          />
        </Form.Item>
        {poCase.lines.map((line) => {
          const name = line.sku_code ?? line.sku_id;
          return (
            <Form.Item
              key={line.sku_id}
              name={["quantities", line.sku_id]}
              label={`Số lượng ${name}${line.variant_label ? ` (${line.variant_label})` : ""}`}
              rules={[{ required: true, message: `Nhập số lượng ${name}` }]}
            >
              <InputNumber min={1} precision={0} />
            </Form.Item>
          );
        })}
        {error && <Alert type="error" showIcon title={error} />}
        <Flex vertical gap="small" align="start">
          <Button
            type="primary"
            htmlType="submit"
            loading={submitting}
            disabled={!online}
          >
            Tạo PO
          </Button>
          {!online && <Typography.Text>{OFFLINE}</Typography.Text>}
        </Flex>
      </Form>
    </Card>
  );
}
