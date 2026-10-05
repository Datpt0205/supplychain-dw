"use client";

import { useCallback, useEffect, useState } from "react";
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
  Modal,
  Result,
  Select,
  Skeleton,
  Table,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ArrowLeftOutlined, ReloadOutlined } from "@ant-design/icons";
import type {
  CaseDocument,
  ProductActionOption,
  ProductCaseDetail,
  ProductCaseTransition,
  SampleRound,
} from "@dw/api-client";
import {
  CaseDocumentsCard,
  DOC_TYPE_LABEL,
} from "../../../../components/supply-chain/case-documents-card";
import {
  PRODUCT_ACTION_LABEL,
  PRODUCT_DEV_STATE_LABEL,
  ProductDevStateTag,
  SAMPLE_RESULT_LABEL,
  dutyLock,
} from "../../../../components/supply-chain/product-case-labels";
import { useAuth } from "../../../../lib/auth/auth-context";
import { formatDateTime } from "../../../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../../../lib/directory";
import { errorCode, errorMessage } from "../../../../lib/error-message";
import { useOnline } from "../../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../../lib/idempotency-key";
import { apiClient } from "../../../../lib/session";

const DOCUMENT_WRITE = "supply_chain.document.write";
const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

type CaseState =
  | { kind: "loading" }
  | { kind: "error"; message: string; code: string | null }
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
      setState({
        kind: "error",
        message: errorMessage(error),
        code: errorCode(error),
      });
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

  return (
    <div className="mx-auto max-w-6xl">
      <Flex vertical gap="middle">
        <Link href="/supply-chain/product-cases">
          <ArrowLeftOutlined aria-hidden /> Danh sách hồ sơ phát triển sản phẩm
        </Link>
        {state.kind === "loading" ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : state.kind === "error" ? (
          <CaseError state={state} onRetry={() => void load()} />
        ) : (
          <CaseView
            detail={state.detail}
            historyTick={historyTick}
            onStep={afterStep}
          />
        )}
      </Flex>
    </div>
  );
}

function CaseError({
  state,
  onRetry,
}: {
  state: { message: string; code: string | null };
  onRetry: () => void;
}) {
  if (state.code === "not_found") {
    // Another tenant's or workspace's case answers not found, never forbidden.
    return (
      <Result
        status="404"
        title="Không tìm thấy hồ sơ"
        subTitle="Hồ sơ này không có trong workspace đang mở."
      />
    );
  }
  if (state.code === "permission_denied") {
    return (
      <Result
        status="403"
        title="Bạn chưa được xem hồ sơ phát triển sản phẩm"
        subTitle="Cần một vai Supply Chain có quyền xem hồ sơ. Liên hệ quản trị workspace."
      />
    );
  }
  return (
    <Result
      status="500"
      title="Không tải được hồ sơ"
      subTitle={state.message}
      extra={
        <Button icon={<ReloadOutlined aria-hidden />} onClick={onRetry}>
          Thử lại
        </Button>
      }
    />
  );
}

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
  const [documentsTick, setDocumentsTick] = useState(0);
  const who = (userId: string | null) =>
    userId === principalId ? "Bạn" : (memberName(members, userId) ?? "—");

  return (
    <Flex vertical gap="middle">
      <Flex vertical gap="small">
        <Typography.Text>
          Hồ sơ phát triển sản phẩm · {detail.proposal_code}
        </Typography.Text>
        <Typography.Title level={3}>{detail.product_name}</Typography.Title>
        <Flex wrap gap="small" align="center">
          <ProductDevStateTag state={detail.state} />
          {detail.interrupted_state && (
            <Typography.Text>
              tạm dừng tại {PRODUCT_DEV_STATE_LABEL[detail.interrupted_state]}
            </Typography.Text>
          )}
        </Flex>
      </Flex>

      <NextSteps
        detail={detail}
        onStep={onStep}
        documentsTick={documentsTick}
      />

      <Card title="Thông tin hồ sơ">
        <Descriptions
          column={{ xs: 1, sm: 2, lg: 3 }}
          items={[
            {
              key: "code",
              label: "Mã đề xuất",
              children: detail.proposal_code,
            },
            { key: "category", label: "Category", children: detail.category },
            {
              key: "supplier",
              label: "NCC",
              children:
                detail.supplier_name ?? "Chưa chọn (chọn khi yêu cầu mẫu)",
            },
            { key: "pic", label: "PIC", children: who(detail.pic_user_id) },
            {
              key: "round",
              label: "Vòng mẫu",
              children:
                detail.sample_round === 0
                  ? "Chưa nhận mẫu"
                  : detail.sample_round,
            },
            {
              key: "created",
              label: "Tạo lúc",
              children: formatDateTime(detail.created_at),
            },
          ]}
        />
      </Card>

      <RoundsCard rounds={detail.rounds} who={who} />
      <HistoryCard caseId={detail.id} tick={historyTick} who={who} />
      <CaseDocumentsCard
        caseKind="product"
        caseId={detail.id}
        canUpload={hasScope(DOCUMENT_WRITE)}
        onUploaded={() => setDocumentsTick((tick) => tick + 1)}
      />
    </Flex>
  );
}

/** The steps the case accepts now. A step whose duty the viewer lacks is
 * locked, with the reason beside it as well as in its tooltip. */
function NextSteps({
  detail,
  onStep,
  documentsTick,
}: {
  detail: ProductCaseDetail;
  onStep: () => void;
  documentsTick: number;
}) {
  const { hasScope } = useAuth();
  const online = useOnline();
  const [open, setOpen] = useState<ProductActionOption | null>(null);

  const lockOf = (option: ProductActionOption): string | null =>
    !hasScope(option.required_scope)
      ? dutyLock(option.required_scope)
      : !online
        ? OFFLINE
        : null;

  return (
    <Card title="Bước tiếp theo">
      <Flex vertical gap="middle">
        {detail.state === "pending_bod_review" && (
          <Alert
            type="info"
            showIcon
            message="Mẫu đã đạt; hồ sơ chờ BGĐ duyệt."
            description="Bước BGĐ duyệt mẫu (bước 6) chưa có trên hệ thống. Trong lúc chờ, chỉ báo ngoại lệ hoặc hủy hồ sơ được."
          />
        )}
        {detail.actions.length === 0 ? (
          <Typography.Text>
            Hồ sơ đã kết thúc; không còn bước nào.
          </Typography.Text>
        ) : (
          <Flex wrap gap="small">
            {detail.actions.map((option) => {
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
        {detail.actions
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

interface StepValues {
  supplierName?: string;
  reason?: string;
  documentId?: string;
}

/**
 * The form one step needs, built from what the server says it takes: a
 * supplier, a reason, a document of a given type. Documents offered are this
 * case's of that type uploaded since the current round opened. That filter is
 * a second copy of a server rule, kept on purpose and only to narrow the
 * picker: the owner is `ProductDevelopmentCase._round_document` (domain), which
 * re-checks the chosen paper on the step and refuses any other with the type
 * it is missing. A change to that rule changes this filter in the same commit.
 * `Date.parse` keeps milliseconds where Postgres keeps microseconds, so a paper
 * uploaded under 1 ms before the round opened is offered and then refused:
 * the copy can only fail closed.
 */
function StepModal({
  detail,
  option,
  documentsTick,
  onClose,
  onDone,
}: {
  detail: ProductCaseDetail;
  option: ProductActionOption;
  documentsTick: number;
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
  const currentRound = detail.rounds.find(
    (r) => r.round_no === detail.sample_round,
  );

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
              currentRound !== undefined &&
              Date.parse(d.uploaded_at) >= Date.parse(currentRound.opened_at),
          ),
        );
      })
      .catch((caught: unknown) => {
        if (!cancelled) setDocumentsError(errorMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [detail.id, documentType, currentRound, documentsTick]);

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
      await apiClient().takeProductCaseStep(
        detail.id,
        {
          action: option.action,
          reason: values.reason,
          supplierName: values.supplierName,
          documentId: values.documentId,
        },
        key,
      );
      void message.success(`Đã ghi: ${label}.`);
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
            message={`Còn ${invalid} trường cần sửa`}
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
            extra={`Chỉ ${DOC_TYPE_LABEL[documentType]} của hồ sơ này, tải lên từ khi vòng mẫu ${detail.sample_round} bắt đầu.`}
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
          <Alert type="error" showIcon message={documentsError} />
        )}
        {noDocument && option.document_required && (
          <Typography.Paragraph>
            Chưa có {DOC_TYPE_LABEL[documentType]} nào cho vòng mẫu này. Tải lên
            ở mục Chứng từ của hồ sơ rồi chọn lại.
          </Typography.Paragraph>
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
        {error && <Alert type="error" showIcon message={error} />}
      </Form>
    </Modal>
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
      title: "Nhận mẫu lúc",
      dataIndex: "opened_at",
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "Kết quả",
      dataIndex: "result",
      render: (value: SampleRound["result"]) =>
        value ? SAMPLE_RESULT_LABEL[value] : "Đang test",
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

type HistoryState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; transitions: ProductCaseTransition[] };

function HistoryCard({
  caseId,
  tick,
  who,
}: {
  caseId: string;
  tick: number;
  who: (userId: string | null) => string;
}) {
  const [history, setHistory] = useState<HistoryState>({ kind: "loading" });

  const load = useCallback(async () => {
    try {
      setHistory({
        kind: "ready",
        transitions: await apiClient().listProductCaseTransitions(caseId),
      });
    } catch (error) {
      setHistory({ kind: "error", message: errorMessage(error) });
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load, tick]);

  return (
    <Card title="Lịch sử">
      {history.kind === "loading" ? (
        <Skeleton active paragraph={{ rows: 3 }} />
      ) : history.kind === "error" ? (
        <Alert
          type="error"
          showIcon
          message={history.message}
          action={
            <Button size="small" onClick={() => void load()}>
              Thử lại
            </Button>
          }
        />
      ) : (
        <Timeline
          items={history.transitions.map((t) => ({
            key: `${t.occurred_at}-${t.action}`,
            children: (
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
                <Typography.Text>
                  {who(t.actor_id)} · {formatDateTime(t.occurred_at)}
                </Typography.Text>
              </Flex>
            ),
          }))}
        />
      )}
    </Card>
  );
}
