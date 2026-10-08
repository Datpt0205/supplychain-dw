"use client";

import { useCallback, useEffect, useState } from "react";
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
  Select,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import { RegionState, StatusTag, type StatusTone } from "@dw/ui";
import { LoadError } from "../load-error";
import type {
  CaseDocument,
  PackagingAction,
  PackagingDesign,
  PackagingStepOption,
  PreProductionTest,
  ReviewStatus,
} from "@dw/api-client";
import { formatDateTime, formatDateTimeFull } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { DOC_TYPE_LABEL } from "./case-documents-card";
import { CASE_STATE_LABEL } from "./case-state-badge";
import { CASE_DUTY_LABEL } from "./product-case-labels";

/** The one table of names for step 12's sub-steps (slice PK). */
export const PACKAGING_ACTION_LABEL: Record<PackagingAction, string> = {
  approve_colour: "Duyệt mẫu màu",
  request_colour_revision: "Yêu cầu sửa màu",
  approve_design: "Duyệt thiết kế bao bì",
  request_design_revision: "Yêu cầu sửa thiết kế",
  receive_pre_production_sample: "Nhận mẫu trước SX",
  pass_pre_production_test: "Đạt test trước SX",
  fail_pre_production_test: "Không đạt test trước SX",
};

const REVIEW_LABEL: Record<ReviewStatus, [string, StatusTone]> = {
  pending: ["Chờ duyệt", "gray"],
  revision_requested: ["Đang sửa", "warn"],
  approved: ["Đã duyệt", "ok"],
};

const TEST_LABEL: Record<PreProductionTest, [string, StatusTone]> = {
  pending: ["Chưa test", "gray"],
  passed: ["Đạt", "ok"],
  failed: ["Không đạt", "err"],
};

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
const REPORT = "pre_production_test_report" as const;

/**
 * Step 12's colour, packaging and pre-production sub-flow on a PO case: the
 * four sub-steps, the history, and a button per step. Who may take a step is
 * the server's answer (`allowed`, the step's own check); the order the steps
 * follow is the server's too, so a step out of order is refused with its
 * sentence rather than hidden by a second copy of the rule here.
 */
export function PackagingDesignCard({
  caseId,
  onStep,
}: {
  caseId: string;
  onStep: () => void;
}) {
  const online = useOnline();
  const resource = useCachedResource(
    `supply-chain:po-case:${caseId}:packaging-design`,
    useCallback(() => apiClient().getPackagingDesign(caseId), [caseId]),
  );
  const [open, setOpen] = useState<PackagingStepOption | null>(null);
  const design = resource.data;

  if (!design) {
    return (
      <Card title="Thiết kế màu và bao bì (bước 12)">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }

  const inStep = design.case_state === "pre_production";
  const lockOf = (option: PackagingStepOption): string | null =>
    !option.allowed
      ? `Bước này cần nhiệm vụ ${CASE_DUTY_LABEL[option.duty]}; vai của bạn chưa có.`
      : !inStep
        ? `Chỉ làm khi Hồ sơ PO ở ${CASE_STATE_LABEL.pre_production}.`
        : !online
          ? OFFLINE
          : null;
  const [colour, colourTone] = REVIEW_LABEL[design.colour_status];
  const [layout, layoutTone] = REVIEW_LABEL[design.design_status];
  const [test, testTone] = TEST_LABEL[design.pre_production_test];

  return (
    <Card title="Thiết kế màu và bao bì (bước 12)">
      <Flex vertical gap="middle">
        {design.require_pre_production_test && (
          <Alert
            type="info"
            showIcon
            title="Hồ sơ chỉ vào Sản xuất khi R&D đạt test trước sản xuất."
          />
        )}
        <Descriptions
          size="small"
          column={{ xs: 1, md: 2 }}
          items={[
            {
              key: "colour",
              label: "Mẫu màu",
              children: <StatusTag tone={colourTone}>{colour}</StatusTag>,
            },
            {
              key: "design",
              label: "Thiết kế bao bì",
              children: <StatusTag tone={layoutTone}>{layout}</StatusTag>,
            },
            {
              key: "sample",
              label: "Mẫu trước SX",
              children: design.pre_production_sample_received_at
                ? `Đã nhận ${formatDateTimeFull(design.pre_production_sample_received_at)}`
                : "Chưa nhận",
            },
            {
              key: "test",
              label: "Test trước SX (R&D)",
              children: <StatusTag tone={testTone}>{test}</StatusTag>,
            },
          ]}
        />
        <Flex wrap gap="small">
          {design.steps.map((option) => {
            const lock = lockOf(option);
            const danger =
              option.action.startsWith("request_") ||
              option.action === "fail_pre_production_test";
            const button = (
              <Button
                key={option.action}
                danger={danger}
                disabled={lock !== null}
                onClick={() => setOpen(option)}
              >
                {PACKAGING_ACTION_LABEL[option.action]}
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
        {/* A disabled button shows nothing on tap: each reason also in words. */}
        {[...new Set(design.steps.map(lockOf).filter((l) => l !== null))].map(
          (lock) => (
            <Typography.Text key={lock}>{lock}</Typography.Text>
          ),
        )}
        <History design={design} />
      </Flex>
      {open && (
        <StepModal
          caseId={caseId}
          option={open}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null);
            resource.reload();
            onStep();
          }}
        />
      )}
    </Card>
  );
}

function History({ design }: { design: PackagingDesign }) {
  if (design.history.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description="Chưa bước con nào được ghi."
      />
    );
  }
  return (
    <Timeline
      items={design.history.map((event, index) => ({
        // A history row has no id of its own; the server orders them.
        key: index,
        content: (
          <Flex vertical gap={2}>
            <Typography.Text strong>
              {PACKAGING_ACTION_LABEL[event.action]}
            </Typography.Text>
            <Typography.Text type="secondary">
              {formatDateTimeFull(event.occurred_at)}
              {event.reason && ` · ${event.reason}`}
            </Typography.Text>
            {event.note && <Typography.Text>{event.note}</Typography.Text>}
          </Flex>
        ),
      }))}
    />
  );
}

interface StepValues {
  reason?: string;
  documentId?: string;
}

function StepModal({
  caseId,
  option,
  onClose,
  onDone,
}: {
  caseId: string;
  option: PackagingStepOption;
  onClose: () => void;
  onDone: () => void;
}) {
  const [form] = Form.useForm<StepValues>();
  const { message } = App.useApp();
  const attemptKey = useAttemptKey();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reports, setReports] = useState<CaseDocument[] | null>(null);
  const label = PACKAGING_ACTION_LABEL[option.action];

  useEffect(() => {
    if (!option.requires_document) return;
    let cancelled = false;
    apiClient()
      .listCaseDocuments(caseId)
      .then((all) => {
        if (!cancelled) setReports(all.filter((d) => d.doc_type === REPORT));
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(errorMessage(caught));
      });
    return () => {
      cancelled = true;
    };
  }, [caseId, option.requires_document]);

  const submit = async (values: StepValues) => {
    const input = {
      action: option.action,
      reason: values.reason,
      documentId: values.documentId,
    };
    setSubmitting(true);
    setError(null);
    try {
      await apiClient().takePackagingStep(caseId, input, attemptKey(input));
      void message.success(`Đã ghi: ${label}.`);
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={label}
      open
      onCancel={onClose}
      okText={label}
      cancelText="Đóng"
      confirmLoading={submitting}
      onOk={() => form.submit()}
    >
      <Form<StepValues>
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        onFinish={(values) => void submit(values)}
      >
        {option.requires_reason && (
          <Form.Item
            name="reason"
            label="Lý do"
            rules={[
              { required: true, whitespace: true, message: "Nhập lý do" },
              { max: 2000, message: "Lý do tối đa 2000 ký tự" },
            ]}
          >
            <Input.TextArea rows={3} />
          </Form.Item>
        )}
        {option.requires_document && (
          <Form.Item
            name="documentId"
            label={DOC_TYPE_LABEL[REPORT]}
            extra={`Tải ${DOC_TYPE_LABEL[REPORT]} lên ở mục Chứng từ của hồ sơ này, sau khi nhận mẫu trước SX.`}
            rules={[
              { required: true, message: `Chọn ${DOC_TYPE_LABEL[REPORT]}` },
            ]}
          >
            <Select
              aria-label={DOC_TYPE_LABEL[REPORT]}
              loading={reports === null && error === null}
              options={(reports ?? []).map((d) => ({
                value: d.id,
                label: `${d.filename} · v${d.version} · ${formatDateTime(d.uploaded_at)}`,
              }))}
            />
          </Form.Item>
        )}
        {!option.requires_reason && !option.requires_document && (
          <Typography.Paragraph>
            Ghi bước “{label}” cho hồ sơ này?
          </Typography.Paragraph>
        )}
        {error && <Alert type="error" showIcon title={error} />}
      </Form>
    </Modal>
  );
}
