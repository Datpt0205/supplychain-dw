"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Select,
  Skeleton,
  Steps,
  Table,
  Timeline,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { TableColumnsType, UploadFile } from "antd";
import { PaperClipOutlined, UploadOutlined } from "@ant-design/icons";
import { PageHeader, RegionState, stateForError } from "@dw/ui";
import { LoadError } from "../../../../components/load-error";
import { LoadMore } from "../../../../components/load-more";
import {
  ApiError,
  type CaseDocument,
  type DocumentType,
  type PendingReview,
  type ProductActionOption,
  type ProductCaseDetail,
  type SampleRound,
} from "@dw/api-client";
import {
  ACCEPT,
  CaseDocumentsCard,
  DOC_TYPE_LABEL,
  NO_WRITE_REASON,
  useCaseDocumentUpload,
} from "../../../../components/supply-chain/case-documents-card";
import {
  CASE_STATE_LABEL,
  stepLabel,
} from "../../../../components/supply-chain/case-state-badge";
import {
  CaseSummary,
  type SummaryCell,
} from "../../../../components/supply-chain/case-summary";
import {
  CODING_ACTIONS,
  ItemCodingCard,
  showsCoding,
} from "../../../../components/supply-chain/item-coding-card";
import { Bm04ProfileCard } from "../../../../components/supply-chain/bm04-profile-card";
import { supplyChainCrumbs } from "../../../../components/supply-chain/crumbs";
import { DraftsCard } from "../../../../components/supply-chain/drafts-card";
import { SupplierMessagesCard } from "../../../../components/supply-chain/supplier-messages-card";
import { SampleChecklistCard } from "../../../../components/supply-chain/sample-checklist-card";
import { StepProposalCard } from "../../../../components/supply-chain/step-proposal-card";
import {
  categoryLabel,
  useProductCategories,
} from "../../../../components/supply-chain/product-categories";
import {
  SlaStatusTag,
  milestoneLabel,
  slaStatusLabel,
} from "../../../../components/supply-chain/sla-status-badge";
import {
  PRODUCT_ACTION_LABEL,
  PRODUCT_DEV_STATE_LABEL,
  PRODUCT_DEV_STATE_META,
  ProductDevStateTag,
  SampleResultTag,
  dutyLock,
  unmetLock,
} from "../../../../components/supply-chain/product-case-labels";
import { useAuth } from "../../../../lib/auth/auth-context";
import {
  formatDateTime,
  formatDateTimeFull,
  VN_TIME,
} from "../../../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../../../lib/directory";
import { errorMessage, toRegionError } from "../../../../lib/error-message";
import { useOnline } from "../../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../../lib/idempotency-key";
import { apiClient } from "../../../../lib/session";
import { useCachedPages } from "../../../../lib/use-cached-pages";

const DOCUMENT_WRITE = "supply_chain.document.write";
const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

type CaseState =
  | { kind: "loading" }
  | { kind: "error"; error: unknown }
  | { kind: "ready"; detail: ProductCaseDetail };

/**
 * One product-development case: where it stands, what can happen next and
 * who may do it, its sample rounds, its history and its documents. Every step
 * button comes from the server's list of steps the case accepts, each with the
 * scope its duty needs; a step the viewer's roles lack is drawn locked with
 * the reason. The server refuses it anyway.
 */
export default function ProductCasePage() {
  const { id } = useParams<{ id: string }>();
  const [state, setState] = useState<CaseState>({ kind: "loading" });
  const [historyTick, setHistoryTick] = useState(0);

  const load = useCallback(async () => {
    try {
      setState({ kind: "ready", detail: await apiClient().getProductCase(id) });
    } catch (error) {
      setState({ kind: "error", error });
    }
  }, [id]);

  useEffect(() => {
    setState({ kind: "loading" });
    void load();
  }, [load]);

  const afterStep = () => {
    void load();
    setHistoryTick((tick) => tick + 1);
  };

  if (state.kind !== "ready") {
    return (
      <div className="mx-auto max-w-6xl">
        <PageHeader
          breadcrumb={LIST_CRUMBS}
          title="Hồ sơ phát triển sản phẩm"
        />
        {state.kind === "loading" ? (
          <RegionState kind="loading" />
        ) : stateForError(toRegionError(state.error)).kind === "forbidden" ? (
          <RegionState
            kind="forbidden"
            title="Bạn chưa được xem hồ sơ phát triển sản phẩm"
            description="Cần một vai Supply Chain có quyền xem hồ sơ. Liên hệ quản trị workspace."
          />
        ) : (
          <LoadError error={state.error} onRetry={() => void load()} />
        )}
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-6xl">
      <CaseView
        detail={state.detail}
        historyTick={historyTick}
        onStep={afterStep}
      />
    </div>
  );
}

const LIST_CRUMBS = supplyChainCrumbs({
  title: "Phát triển SP",
  href: "/supply-chain/product-cases",
});

function CaseView({
  detail,
  historyTick,
  onStep,
}: {
  detail: ProductCaseDetail;
  historyTick: number;
  onStep: () => void;
}) {
  const { hasScope, principalId } = useAuth();
  const members = useWorkspaceMembers();
  const categories = useProductCategories().data;
  const [documentsTick, setDocumentsTick] = useState(0);
  // A paper uploaded from a step's form: the documents card lists it too.
  const [stepUploads, setStepUploads] = useState(0);
  const who = (userId: string | null) =>
    userId === principalId ? "Bạn" : (memberName(members, userId) ?? "—");

  const step = PRODUCT_DEV_STATE_META[detail.state].step;
  const cells: SummaryCell[] = [
    {
      key: "step",
      label: "Bước",
      value: stepLabel(step) ?? PRODUCT_DEV_STATE_LABEL[detail.state],
      sub: detail.interrupted_state
        ? `Tạm dừng tại ${PRODUCT_DEV_STATE_LABEL[detail.interrupted_state]}`
        : undefined,
    },
    {
      key: "round",
      label: "Vòng mẫu",
      value:
        detail.sample_round === 0
          ? "Chưa nhận mẫu"
          : `Vòng ${detail.sample_round}`,
    },
    {
      key: "supplier",
      label: "NCC",
      value: detail.supplier_name ?? "Chưa chọn",
      sub: detail.supplier_name ? undefined : "Chọn khi yêu cầu mẫu (bước 2)",
    },
    { key: "pic", label: "PIC", value: who(detail.pic_user_id) },
    {
      // The current step's SLA under the case's Category (ticket 06).
      key: "sla",
      label: "SLA",
      value: slaStatusLabel(detail.sla.status),
      sub:
        detail.sla.status === "not_applicable"
          ? "Bước này không có mốc SLA"
          : `Mốc ${milestoneLabel(detail.sla.milestone)} · ${detail.sla.age_days} ngày${detail.sla.threshold_days !== null ? ` / hạn ${detail.sla.threshold_days} ngày` : ""}`,
      tone: detail.sla.status === "breached" ? "err" : undefined,
    },
  ];

  return (
    <>
      <PageHeader
        breadcrumb={[...LIST_CRUMBS, { title: detail.proposal_code }]}
        title={detail.product_name}
        tags={
          <>
            <Typography.Text type="secondary">
              Hồ sơ phát triển sản phẩm ·{" "}
              <Typography.Text code>{detail.proposal_code}</Typography.Text>
            </Typography.Text>
            <ProductDevStateTag state={detail.state} />
            {detail.sla.status !== "not_applicable" && (
              <SlaStatusTag status={detail.sla.status} />
            )}
          </>
        }
        subtitle={`Category ${categoryLabel(categories, detail.category)} · tạo lúc ${formatDateTimeFull(detail.created_at)}`}
      />
      <Flex vertical gap="middle">
        <CaseSummary label="Tóm tắt hồ sơ" cells={cells} />

        <NextSteps
          detail={detail}
          onStep={onStep}
          documentsTick={documentsTick}
          onPaperUploaded={() => setStepUploads((tick) => tick + 1)}
        />

        {showsCoding(detail) && (
          <ItemCodingCard detail={detail} onStep={onStep} />
        )}

        {detail.state === "sample_testing" && (
          <SampleChecklistCard caseId={detail.id} />
        )}
        <StepProposalCard caseId={detail.id} onDecided={onStep} />
        <Bm04ProfileCard caseId={detail.id} />
        <DraftsCard caseKind="product" caseId={detail.id} />
        <SupplierMessagesCard caseKind="product" caseId={detail.id} />
        <RoundsCard rounds={detail.rounds} who={who} />
        <HistoryCard caseId={detail.id} tick={historyTick} who={who} />
        <CaseDocumentsCard
          caseKind="product"
          caseId={detail.id}
          canUpload={hasScope(DOCUMENT_WRITE)}
          onUploaded={() => setDocumentsTick((tick) => tick + 1)}
          refreshTick={stepUploads}
        />
      </Flex>
    </>
  );
}

/** The steps the case accepts now, less step 9's coding (its own card) and
 * ĐẶT HÀNG (its own route and confirmation). A step whose duty the viewer
 * lacks, or that the case is not ready for (the server's `unmet`), is locked,
 * with the reason beside it as well as in its tooltip. */
function NextSteps({
  detail,
  onStep,
  documentsTick,
  onPaperUploaded,
}: {
  detail: ProductCaseDetail;
  onStep: () => void;
  documentsTick: number;
  onPaperUploaded: () => void;
}) {
  const { hasScope } = useAuth();
  const online = useOnline();
  const [open, setOpen] = useState<ProductActionOption | null>(null);
  const steps = detail.actions.filter(
    (option) =>
      !CODING_ACTIONS.includes(option.action) &&
      option.action !== "place_order",
  );
  const placeOrder = detail.actions.find(
    (option) => option.action === "place_order",
  );

  const lockOf = (option: ProductActionOption): string | null =>
    !hasScope(option.required_scope)
      ? dutyLock(option.required_scope)
      : option.unmet.length > 0
        ? unmetLock(option.unmet)
        : !online
          ? OFFLINE
          : null;

  return (
    <Card title="Bước tiếp theo">
      <Flex vertical gap="middle">
        {detail.state === "pending_bod_review" && (
          <BodReviewNotice review={detail.pending_review} />
        )}
        {detail.state === "pending_signoff" && (
          <SignoffNotice review={detail.pending_review} />
        )}
        {detail.state === "ready_to_order" && (
          <Alert
            type="success"
            showIcon
            title="Đã ký đủ; hồ sơ sẵn sàng đặt hàng."
            description="Mã hàng và SKU đã chốt. Bước ĐẶT HÀNG tạo Hồ sơ PO."
          />
        )}
        {detail.state === "ordered" && (
          <Alert
            type="success"
            showIcon
            title="Đã đặt hàng; hồ sơ phát triển kết thúc."
            description={
              detail.po_case_id ? (
                <Flex vertical gap="small">
                  <Typography.Text>
                    Đơn hàng tiếp tục ở Hồ sơ PO: chờ Cung ứng tạo PO (bước 10).
                  </Typography.Text>
                  <Link href={`/supply-chain/po-cases/${detail.po_case_id}`}>
                    Hồ sơ PO
                  </Link>
                </Flex>
              ) : null
            }
          />
        )}
        {placeOrder && (
          <PlaceOrder
            detail={detail}
            lock={lockOf(placeOrder)}
            onStep={onStep}
          />
        )}
        {steps.length === 0 ? (
          placeOrder ? null : (
            <Typography.Text>
              Hồ sơ đã kết thúc; không còn bước nào.
            </Typography.Text>
          )
        ) : (
          <Flex wrap gap="small">
            {steps.map((option) => {
              const lock = lockOf(option);
              const button = (
                <Button
                  key={option.action}
                  type={option.action === "cancel" ? "default" : "primary"}
                  danger={
                    option.action === "cancel" ||
                    option.action === "reject_sample"
                  }
                  disabled={lock !== null}
                  onClick={() => setOpen(option)}
                >
                  {PRODUCT_ACTION_LABEL[option.action]}
                </Button>
              );
              return lock ? (
                <Tooltip key={option.action} title={lock}>
                  <span>{button}</span>
                </Tooltip>
              ) : (
                button
              );
            })}
          </Flex>
        )}
        {steps
          .filter((option) => lockOf(option) !== null)
          .map((option) => (
            <Typography.Text key={`lock-${option.action}`}>
              {PRODUCT_ACTION_LABEL[option.action]}: {lockOf(option)}
            </Typography.Text>
          ))}
      </Flex>
      {open && (
        <StepModal
          detail={detail}
          option={open}
          documentsTick={documentsTick}
          onPaperUploaded={onPaperUploaded}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            onStep();
          }}
        />
      )}
    </Card>
  );
}

/**
 * ĐẶT HÀNG (ADR 0017): the case ends as ordered and, in the same transaction,
 * a PO case opens awaiting its PO number, carrying the PIC, the Category, the
 * NCC and one line per SKU. Taken at its own route after a confirmation; a
 * second press is the server's 409, shown with its sentence. One key per
 * attempt, kept on a retry that never got an answer, so a lost response is
 * not a second order.
 */
function PlaceOrder({
  detail,
  lock,
  onStep,
}: {
  detail: ProductCaseDetail;
  lock: string | null;
  onStep: () => void;
}) {
  const { message, modal } = App.useApp();
  const categoryList = useProductCategories().data;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);
  const label = PRODUCT_ACTION_LABEL.place_order;

  const order = async () => {
    key.current ??= newIdempotencyKey();
    setBusy(true);
    setError(null);
    try {
      await apiClient().placeProductOrder(detail.id, key.current);
      key.current = null;
      void message.success("Đã đặt hàng; Hồ sơ PO đã mở, chờ tạo PO.");
      onStep();
    } catch (caught) {
      setError(errorMessage(caught));
      if (caught instanceof ApiError && caught.status === 409) {
        // The case moved under us: show where it stands now.
        key.current = null;
        onStep();
      }
    } finally {
      setBusy(false);
    }
  };

  const confirm = () => {
    modal.confirm({
      title: `ĐẶT HÀNG cho ${detail.proposal_code}?`,
      content: `Hồ sơ phát triển "${detail.product_name}" kết thúc ở ${PRODUCT_DEV_STATE_LABEL.ordered}. Một Hồ sơ PO mở ở ${CASE_STATE_LABEL.order_requested}, chưa có số PO, mang PIC, Category ${categoryLabel(categoryList, detail.category)}, NCC và ${detail.skus.length} SKU; Cung ứng tạo PO ở bước 10. Không hoàn tác được.`,
      okText: label,
      cancelText: "Chưa đặt",
      autoFocusButton: "cancel",
      onOk: () => order(),
    });
  };

  const button = (
    <Button
      type="primary"
      disabled={lock !== null}
      loading={busy}
      onClick={confirm}
    >
      {label}
    </Button>
  );
  return (
    <Flex vertical gap="small" align="start">
      {lock ? (
        <Tooltip title={lock}>
          <span>{button}</span>
        </Tooltip>
      ) : (
        button
      )}
      {lock && (
        <Typography.Text>
          {label}: {lock}
        </Typography.Text>
      )}
      {error && <Alert type="error" showIcon title={error} />}
    </Flex>
  );
}

/**
 * Step 6 while it waits: who may decide (the scope stamped on the review, as
 * the server returns it; never a list of names) and where: the review's own
 * page, `/approvals/<id>`, which also issues the code a decision on Zalo needs
 * (zalo-channel ticket 05). The case page offers no decision. A case with no review yet says so: the worker
 * raises it, nobody has to pass the sample again.
 */
function BodReviewNotice({ review }: { review: PendingReview | null }) {
  if (!review) {
    return (
      <Alert
        type="warning"
        showIcon
        title="Mẫu đã đạt; chưa trình được BGĐ."
        description="Yêu cầu duyệt chưa tạo được (ví dụ gói đã hết lượt chạy trong ngày). Hệ thống tự trình lại sau ít phút; không cần làm lại bước Mẫu đạt. Trong lúc chờ, chỉ hủy hồ sơ được."
      />
    );
  }
  return (
    <Alert
      type="info"
      showIcon
      title="Mẫu đã đạt; hồ sơ chờ BGĐ duyệt."
      description={
        <Flex vertical gap="small">
          <Typography.Text>
            Chờ người có quyền BGĐ ({review.required_scope ?? "—"}) duyệt, từ{" "}
            {formatDateTimeFull(review.created_at)}. Không duyệt thì hồ sơ bị
            hủy, nhận xét của BGĐ là lý do. Trong lúc chờ, chỉ hủy hồ sơ được.
          </Typography.Text>
          <Link href={`/approvals/${review.approval_id}`}>
            Mở yêu cầu duyệt (quyết trên web hoặc lấy mã quyết qua Zalo)
          </Link>
        </Flex>
      }
    />
  );
}

/**
 * Step 9 while it waits: the sign-off's steps in the order the case was
 * submitted under, which are signed and which is waiting, who may sign it
 * (the scope stamped on it, never names) and where: the step's own page, which
 * also issues the code a decision on Zalo needs. The approval is shown only to
 * its signers and its requester, so "no approval" may also mean this viewer
 * may not see it; the words say both.
 */
function SignoffNotice({ review }: { review: PendingReview | null }) {
  if (!review || review.step_no === null) {
    return (
      <Alert
        type="warning"
        showIcon
        title="Đã trình ký; chưa thấy yêu cầu ký."
        description="Yêu cầu ký chỉ hiện với người ký và người trình. Nếu bạn là một trong hai: yêu cầu chưa tạo được (ví dụ gói đã hết lượt chạy trong ngày), hệ thống tự trình lại sau ít phút; không cần trình lại. Trong lúc chờ ký, chỉ hủy hồ sơ được."
      />
    );
  }
  const current = review.step_no - 1;
  return (
    <Alert
      type="info"
      showIcon
      title={`Hồ sơ chờ ký: bước ${review.step_no}/${review.steps.length}, ${review.step_label ?? ""}.`}
      description={
        <Flex vertical gap="small">
          <Steps
            size="small"
            current={current}
            items={review.steps.map((step, index) => ({
              title: step.label,
              description:
                index < current
                  ? "Đã ký"
                  : index === current
                    ? "Đang chờ ký"
                    : "Chưa tới",
            }))}
          />
          <Typography.Text>
            Chờ người có quyền {review.step_label} (
            {review.required_scope ?? "—"}) ký, từ{" "}
            {formatDateTimeFull(review.created_at)}. Một bước không ký thì hồ sơ
            về {PRODUCT_DEV_STATE_LABEL.item_coding}, nhận xét là lý do; mã hàng
            và SKU giữ nguyên. Trong lúc chờ ký, chỉ hủy hồ sơ được.
          </Typography.Text>
          <Link href={`/approvals/${review.approval_id}`}>
            Mở yêu cầu ký (quyết trên web hoặc lấy mã quyết qua Zalo)
          </Link>
        </Flex>
      }
    />
  );
}

interface StepValues {
  supplierName?: string;
  reason?: string;
  documentId?: string;
}

/**
 * The form one step needs, built from what the server says it takes: a
 * supplier, a reason, a document of a given type. Documents offered are this
 * case's of that type uploaded since `documents_since`, the bound the server
 * gives with the option (when the round opened, or when the case reached the
 * step) and checks again on the step (`ProductDevelopmentCase._step_document`,
 * domain); the page only narrows the picker with it, and offers nothing when
 * the server gives no bound. `Date.parse` keeps milliseconds where Postgres
 * keeps microseconds, so a paper uploaded under 1 ms before the bound is
 * offered and then refused: the comparison can only fail closed. A step's
 * paper can be uploaded right here, and is then chosen.
 */
function StepModal({
  detail,
  option,
  documentsTick,
  onPaperUploaded,
  onClose,
  onDone,
}: {
  detail: ProductCaseDetail;
  option: ProductActionOption;
  documentsTick: number;
  onPaperUploaded: () => void;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<StepValues>();
  const { message } = App.useApp();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(0);
  const [attempt, setAttempt] = useState<{
    key: string;
    values: string;
  } | null>(null);
  const [documents, setDocuments] = useState<CaseDocument[] | null>(null);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const label = PRODUCT_ACTION_LABEL[option.action];
  const documentType = option.document_type;
  const since = option.documents_since;

  useEffect(() => {
    if (!documentType) return;
    let cancelled = false;
    apiClient()
      .listProductCaseDocuments(detail.id)
      .then((all) => {
        if (cancelled) return;
        setDocuments(
          all.filter(
            (d) =>
              d.doc_type === documentType &&
              since !== null &&
              Date.parse(d.uploaded_at) >= Date.parse(since),
          ),
        );
      })
      .catch((caught: unknown) => {
        if (!cancelled) setDocumentsError(errorMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [detail.id, documentType, since, documentsTick]);

  const paperUploaded = (created: CaseDocument) => {
    setDocuments((current) => [...(current ?? []), created]);
    form.setFieldValue("documentId", created.id);
    void message.success(
      `Đã tải lên ${DOC_TYPE_LABEL[created.doc_type]}, phiên bản ${created.version}; đã chọn cho bước này.`,
    );
    onPaperUploaded();
  };

  const submit = async (values: StepValues) => {
    setInvalid(0);
    const fingerprint = JSON.stringify(values);
    const key =
      attempt && attempt.values === fingerprint
        ? attempt.key
        : newIdempotencyKey();
    setAttempt({ key, values: fingerprint });
    setSubmitting(true);
    setError(null);
    try {
      const step = await apiClient().takeProductCaseStep(
        detail.id,
        {
          action: option.action,
          reason: values.reason,
          supplierName: values.supplierName,
          documentId: values.documentId,
        },
        key,
      );
      if (step.review === "not_raised") {
        // Recorded; the approval is not. The case page says so too, and the
        // worker raises it: nothing for this person to redo.
        const what =
          option.action === "submit_for_signoff"
            ? "Chưa tạo được yêu cầu ký"
            : "Chưa trình được BGĐ";
        void message.warning(
          `Đã ghi: ${label}. ${what}; hệ thống sẽ tự trình lại.`,
        );
      } else {
        void message.success(`Đã ghi: ${label}.`);
      }
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  const noDocument =
    documentType !== null && documents !== null && documents.length === 0;
  return (
    <Modal
      title={`${label} · ${detail.proposal_code}`}
      open
      onCancel={onClose}
      okText={label}
      cancelText="Đóng"
      okButtonProps={{
        danger: option.action === "cancel" || option.action === "reject_sample",
        disabled: option.document_required && noDocument,
      }}
      confirmLoading={submitting}
      onOk={() => form.submit()}
    >
      <Form<StepValues>
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        scrollToFirstError={{ focus: true }}
        onFinish={(values) => void submit(values)}
        onFinishFailed={({ errorFields }) => setInvalid(errorFields.length)}
      >
        {invalid > 0 && (
          <Alert
            type="error"
            showIcon
            title={`Còn ${invalid} trường cần sửa`}
          />
        )}
        {option.takes_supplier && (
          <Form.Item
            name="supplierName"
            label="NCC"
            extra="Nhà cung cấp được liên hệ lấy mẫu ở bước này."
            rules={[
              { required: true, whitespace: true, message: "Nhập tên NCC" },
              { max: 200, message: "Tên NCC tối đa 200 ký tự" },
            ]}
          >
            <Input />
          </Form.Item>
        )}
        {documentType && (
          <Form.Item
            name="documentId"
            label={DOC_TYPE_LABEL[documentType]}
            extra={`Chỉ ${DOC_TYPE_LABEL[documentType]} của hồ sơ này, tải lên từ ${formatDateTimeFull(since)} trở đi.`}
            rules={
              option.document_required
                ? [
                    {
                      required: true,
                      message: `Chọn ${DOC_TYPE_LABEL[documentType]}`,
                    },
                  ]
                : []
            }
          >
            <Select
              aria-label={DOC_TYPE_LABEL[documentType]}
              loading={documents === null && documentsError === null}
              allowClear={!option.document_required}
              options={(documents ?? []).map((d) => ({
                value: d.id,
                label: `${d.filename} · v${d.version} · ${formatDateTime(d.uploaded_at)}`,
              }))}
              virtual={false}
            />
          </Form.Item>
        )}
        {documentsError && (
          <Alert type="error" showIcon title={documentsError} />
        )}
        {noDocument && option.document_required && (
          <Typography.Paragraph>
            Chưa có {DOC_TYPE_LABEL[documentType]} nào cho bước này. Tải lên
            ngay dưới đây; file vừa tải được chọn cho bước.
          </Typography.Paragraph>
        )}
        {documentType && (
          <StepPaperUpload
            caseId={detail.id}
            docType={documentType}
            onUploaded={paperUploaded}
          />
        )}
        {option.reason_required && (
          <Form.Item
            name="reason"
            label={
              option.action === "request_revision"
                ? "Nội dung yêu cầu chỉnh sửa"
                : "Lý do"
            }
            rules={[
              { required: true, whitespace: true, message: "Nhập lý do" },
              { max: 2000, message: "Tối đa 2000 ký tự" },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        )}
        {error && <Alert type="error" showIcon title={error} />}
      </Form>
    </Modal>
  );
}

/**
 * Uploading a step's paper without leaving its form (ticket 03: "tải chứng từ
 * ngay trong luồng"): the type is the step's, the upload is the documents
 * card's (`useCaseDocumentUpload`), and who may upload is the server's
 * document write, drawn locked with the reason for a viewer without it.
 */
function StepPaperUpload({
  caseId,
  docType,
  onUploaded,
}: {
  caseId: string;
  docType: DocumentType;
  onUploaded: (created: CaseDocument) => void;
}) {
  const { hasScope } = useAuth();
  const online = useOnline();
  const { upload, uploading, error, clearError } = useCaseDocumentUpload(
    "product",
    caseId,
  );
  const [file, setFile] = useState<File | null>(null);
  const lock = !hasScope(DOCUMENT_WRITE)
    ? NO_WRITE_REASON
    : !online
      ? OFFLINE
      : null;
  const name = `Tải lên ${DOC_TYPE_LABEL[docType]}`;
  const fileList: UploadFile[] = file
    ? [{ uid: "chosen", name: file.name, status: "done" }]
    : [];
  const send = async () => {
    if (!file) return;
    const created = await upload(docType, file);
    if (!created) return;
    setFile(null);
    onUploaded(created);
  };
  const button = (
    <Button
      icon={<UploadOutlined aria-hidden />}
      loading={uploading}
      disabled={lock !== null || !file}
      onClick={() => void send()}
    >
      {name}
    </Button>
  );
  return (
    <Flex vertical gap="small">
      <Flex wrap gap="small" align="start">
        <Upload
          accept={ACCEPT}
          maxCount={1}
          fileList={fileList}
          disabled={lock !== null}
          beforeUpload={(chosen) => {
            setFile(chosen);
            clearError();
            return false;
          }}
          onRemove={() => {
            setFile(null);
            return true;
          }}
        >
          <Button
            icon={<PaperClipOutlined aria-hidden />}
            disabled={lock !== null}
          >
            Chọn file
          </Button>
        </Upload>
        {lock ? (
          <Tooltip title={lock}>
            <span>{button}</span>
          </Tooltip>
        ) : (
          button
        )}
      </Flex>
      {lock && <Typography.Text>{lock}</Typography.Text>}
      {error && <Alert type="error" showIcon title={error} />}
    </Flex>
  );
}

function RoundsCard({
  rounds,
  who,
}: {
  rounds: SampleRound[];
  who: (userId: string | null) => string;
}) {
  const columns: TableColumnsType<SampleRound> = [
    { title: "Vòng", dataIndex: "round_no", align: "right" },
    {
      title: `Nhận mẫu lúc (${VN_TIME})`,
      dataIndex: "opened_at",
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "Kết quả",
      dataIndex: "result",
      render: (value: SampleRound["result"]) => (
        <SampleResultTag result={value} />
      ),
    },
    {
      title: "Người kết luận",
      dataIndex: "closed_by",
      render: (value: string | null) => (value ? who(value) : "—"),
    },
    {
      title: "Yêu cầu chỉnh sửa",
      dataIndex: "requested_changes",
      render: (value: string | null) =>
        value ? (
          <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }}>
            {value}
          </Typography.Paragraph>
        ) : (
          "—"
        ),
    },
  ];
  return (
    <Card title="Vòng mẫu">
      <Table<SampleRound>
        rowKey="round_no"
        size="small"
        pagination={false}
        scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={rounds}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="Chưa nhận mẫu nào. Vòng 1 bắt đầu khi R&D ghi nhận đã nhận mẫu."
            />
          ),
        }}
      />
    </Card>
  );
}

function HistoryCard({
  caseId,
  tick,
  who,
}: {
  caseId: string;
  tick: number;
  who: (userId: string | null) => string;
}) {
  // Newest first, a page at a time: the page opens where the case is now.
  const history = useCachedPages(
    `supply-chain:product-case:${caseId}:history`,
    useCallback(
      (cursor: string | null) =>
        apiClient().listProductCaseTransitions(caseId, {
          cursor: cursor ?? undefined,
        }),
      [caseId],
    ),
  );
  const { reload } = history;
  const firstTick = useRef(tick);
  useEffect(() => {
    // A step taken on this page moves the case: read its history again.
    if (tick !== firstTick.current) reload();
  }, [tick, reload]);

  return (
    <Card title="Lịch sử">
      {history.loading ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : history.error != null && history.items.length === 0 ? (
        <LoadError compact error={history.error} onRetry={history.reload} />
      ) : history.items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="Hồ sơ chưa qua bước nào."
        />
      ) : (
        <Flex vertical gap="middle">
          <Timeline
            items={history.items.map((t) => ({
              key: `${t.occurred_at}-${t.action}`,
              content: (
                <Flex vertical>
                  <Typography.Text strong>
                    {PRODUCT_ACTION_LABEL[t.action]}
                    {" · "}
                    {t.from_state
                      ? `${PRODUCT_DEV_STATE_LABEL[t.from_state]} → `
                      : ""}
                    {PRODUCT_DEV_STATE_LABEL[t.to_state]}
                  </Typography.Text>
                  {t.reason && <Typography.Text>{t.reason}</Typography.Text>}
                  <Typography.Text type="secondary">
                    {who(t.actor_id)} · {formatDateTimeFull(t.occurred_at)}
                  </Typography.Text>
                </Flex>
              ),
            }))}
          />
          <LoadMore
            hasMore={history.hasMore}
            loading={history.loadingMore}
            onLoadMore={history.loadMore}
            shown={history.items.length}
            noun="bước"
          />
        </Flex>
      )}
    </Card>
  );
}
