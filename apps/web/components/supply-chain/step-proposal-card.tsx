"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Form,
  Input,
  Tooltip,
  Typography,
} from "antd";
import { RegionState, StatusTag } from "@dw/ui";
import type { ProposalResultField } from "@dw/api-client";
import type { DocumentType, ProductAction } from "@dw/contracts";
import { LoadError } from "../load-error";
import { useAuth } from "../../lib/auth/auth-context";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { DOC_TYPE_LABEL } from "./case-documents-card";
import { CASE_DUTY_LABEL, PRODUCT_ACTION_LABEL } from "./product-case-labels";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const CANNOT_DECIDE =
  "Duyệt đề xuất này cần quyền duyệt và đúng duty của bước; vai của bạn chưa có.";
export const STALE =
  "Hồ sơ, bản nháp hoặc chứng từ nguồn đã đổi sau khi AI chuẩn bị; đề xuất này sẽ được chuẩn bị lại.";

/** Why a step could not be prepared, in words (`NotPreparedReason`). */
const NOT_PREPARED_LABEL: Record<string, string> = {
  case_moved: "hồ sơ đã sang bước khác",
  action_document_missing: "chưa có chứng từ của bước này trên hồ sơ",
  source_not_read_yet: "máy đang đọc chứng từ nguồn",
  result_field_unknown: "mẫu chứng từ không có ô kết quả cần nhập",
  run_refused: "hết lượt chạy AI của gói",
  run_failed: "lỗi khi chuẩn bị; hệ thống sẽ thử lại",
};

function reasonText(reason: string | null): string {
  if (!reason) return "";
  const [code = "", subject] = reason.split(":", 2);
  const words = NOT_PREPARED_LABEL[code] ?? reason;
  const document = subject
    ? DOC_TYPE_LABEL[subject as DocumentType]
    : undefined;
  return document ? `${words} (${document})` : words;
}

function docLabel(docType: string): string {
  return DOC_TYPE_LABEL[docType as DocumentType] ?? docType;
}

function dutyLabel(scope: string | null): string {
  if (!scope) return "";
  const duty = scope.replace("supply_chain.duty.", "");
  return CASE_DUTY_LABEL[duty as keyof typeof CASE_DUTY_LABEL] ?? scope;
}

function Suggestion({ field }: { field: ProposalResultField }) {
  if (!field.suggestion) {
    return <Typography.Text>AI không đọc được gợi ý.</Typography.Text>;
  }
  return (
    <Tooltip title={field.suggestion.quote || undefined}>
      <Typography.Text>AI đọc được: “{field.suggestion.value}”</Typography.Text>
    </Tooltip>
  );
}

/**
 * What AI prepared for the case's current step (ADR 0025): the drafts, the
 * documents it read, what code found, and one proposal "move to step X with
 * documents Y". The case moves only when a person holding that step's duty
 * approves. A physical step (a test, a count) shows its result fields EMPTY,
 * with AI's reading beside each: the person types the result, then approves.
 * Any other step can also be decided from the approval page (a Zalo code).
 */
export function StepProposalCard({
  caseId,
  onDecided,
}: {
  caseId: string;
  onDecided: () => void;
}) {
  const { message } = App.useApp();
  const workspaceId = useAuth().active?.workspaceId ?? "";
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:product-case:${caseId}:step-proposal`,
    useCallback(() => apiClient().getStepProposal(caseId), [caseId]),
  );
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [form] = Form.useForm<Record<string, string>>();
  const proposal = resource.data;
  const shown = useRef<string | null>(null);

  // A new proposal starts from empty fields: never AI's reading, never the
  // last proposal's typing.
  useEffect(() => {
    const id = proposal?.approval_id ?? null;
    if (id !== shown.current) {
      shown.current = id;
      form.resetFields();
    }
  }, [proposal?.approval_id, form]);

  if (!proposal) {
    return (
      <Card title="AI đã chuẩn bị">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }
  if (!proposal.prepared) return null;

  const decide = async (approve: boolean) => {
    if (!proposal.approval_id) return;
    setRefusal(null);
    let values: Record<string, string>;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }
    const comment = (values.__comment ?? "").trim();
    const result = Object.fromEntries(
      proposal.result_fields.map((f) => [
        f.name,
        (values[f.name] ?? "").trim(),
      ]),
    );
    const body = {
      approval_id: proposal.approval_id,
      approve,
      comment,
      result: approve && proposal.physical ? result : {},
    };
    setBusy(true);
    try {
      await apiClient().decideStepProposal(caseId, body, attemptKey(body));
      void message.success(
        approve ? "Đã duyệt; hồ sơ chuyển bước" : "Đã không duyệt đề xuất",
      );
      resource.reload();
      onDecided();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const action = proposal.action
    ? (PRODUCT_ACTION_LABEL[proposal.action as ProductAction] ??
      proposal.action)
    : "";
  const lock = !online
    ? OFFLINE
    : proposal.stale
      ? STALE
      : !proposal.can_decide
        ? CANNOT_DECIDE
        : null;

  return (
    <Card title="AI đã chuẩn bị">
      {proposal.status === "none" && (
        <Typography.Text>
          AI chưa chuẩn bị bước này; hệ thống sẽ chuẩn bị trong ít phút.
        </Typography.Text>
      )}
      {proposal.status === "not_prepared" && (
        <Alert
          type="warning"
          showIcon
          title={`Chưa chuẩn bị được: ${reasonText(proposal.reason)}`}
          description="Người giữ duty vẫn làm bước này bằng tay với chứng từ của mình như thường."
        />
      )}
      {proposal.status === "superseded" && (
        <Typography.Text>
          Đề xuất cũ đã được thay; AI đang chuẩn bị lại.
        </Typography.Text>
      )}
      {proposal.status === "rejected" && (
        <Typography.Text>
          Đề xuất của AI đã không được duyệt: {proposal.reason}
        </Typography.Text>
      )}
      {proposal.status === "applied" && (
        <Typography.Text>Đã chuyển bước theo đề xuất của AI.</Typography.Text>
      )}
      {proposal.status === "proposed" && (
        <Flex vertical gap="middle">
          <Flex gap="small" align="center" wrap>
            <Typography.Text strong>Đề xuất: {action}</Typography.Text>
            {proposal.physical && (
              <StatusTag tone="warn">Cần nhập kết quả</StatusTag>
            )}
            {proposal.required_scope && (
              <StatusTag tone="gray">{`Người duyệt: ${dutyLabel(proposal.required_scope)}`}</StatusTag>
            )}
          </Flex>
          {proposal.drafts.length > 0 && (
            <Typography.Text>
              Chứng từ AI soạn:{" "}
              {proposal.drafts
                .map(
                  (d) =>
                    `${docLabel(d.doc_type)} v${d.version}${d.gaps.length ? ` (${d.gaps.length} ô thiếu)` : ""}`,
                )
                .join(", ")}
            </Typography.Text>
          )}
          {proposal.sources.length > 0 && (
            <Typography.Text>
              Chứng từ đã đọc:{" "}
              {proposal.sources
                .map(
                  (s) =>
                    `${docLabel(s.doc_type)}${s.document_id ? "" : " (chưa có)"}`,
                )
                .join(", ")}
            </Typography.Text>
          )}
          {proposal.findings.length > 0 && (
            <Alert
              type="info"
              showIcon
              title="Máy kiểm thấy"
              description={
                <ul className="m-0 pl-4">
                  {proposal.findings.map((f, i) => (
                    <li key={`${f.code}-${i}`}>
                      {docLabel(f.subject)}: {f.message}
                    </li>
                  ))}
                </ul>
              }
            />
          )}
          {proposal.stale && <Alert type="warning" showIcon title={STALE} />}
          <Form form={form} layout="vertical" disabled={busy || lock !== null}>
            {proposal.physical &&
              proposal.result_fields.map((f) => (
                <Form.Item
                  key={f.name}
                  name={f.name}
                  label={f.label}
                  extra={<Suggestion field={f} />}
                  rules={[
                    {
                      required: true,
                      whitespace: true,
                      message: "Nhập kết quả",
                    },
                  ]}
                >
                  <Input
                    type={f.kind === "date" ? "date" : "text"}
                    aria-label={f.label}
                    autoComplete="off"
                  />
                </Form.Item>
              ))}
            <Form.Item
              name="__comment"
              label="Nhận xét (bắt buộc)"
              rules={[
                { required: true, whitespace: true, message: "Ghi nhận xét" },
              ]}
            >
              <Input.TextArea rows={2} maxLength={2000} aria-label="Nhận xét" />
            </Form.Item>
          </Form>
          {refusal && <Alert type="error" showIcon title={refusal} />}
          <Flex gap="small" wrap align="center">
            <Tooltip title={lock ?? undefined}>
              <Button
                type="primary"
                loading={busy}
                disabled={lock !== null}
                onClick={() => void decide(true)}
              >
                Duyệt chuyển bước
              </Button>
            </Tooltip>
            <Tooltip title={lock ?? undefined}>
              <Button
                danger
                disabled={lock !== null || busy}
                onClick={() => void decide(false)}
              >
                Không duyệt
              </Button>
            </Tooltip>
            {!proposal.physical && proposal.approval_id && (
              <Link
                href={`/approvals/${proposal.approval_id}?workspace=${encodeURIComponent(workspaceId)}`}
              >
                Mở trang duyệt (lấy mã duyệt qua Zalo)
              </Link>
            )}
          </Flex>
          {lock && <Typography.Text>{lock}</Typography.Text>}
          {proposal.physical && (
            <Typography.Text>
              Bước có việc vật lý: chỉ duyệt trên web, sau khi nhập kết quả.
              Zalo chỉ báo.
            </Typography.Text>
          )}
        </Flex>
      )}
    </Card>
  );
}
