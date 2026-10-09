"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  List,
  Select,
  Tooltip,
  Typography,
} from "antd";
import { RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type { POStepProposal, POStepResult } from "@dw/api-client";
import type { DocumentType } from "@dw/contracts";
import { LoadError } from "../load-error";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { DOC_TYPE_LABEL } from "./case-documents-card";
import {
  FILL_RESULTS,
  HIDDEN_SUGGESTION,
  OFFLINE_STEP,
  PO_STEP_LABEL,
  RESULT_OPTION_LABEL,
} from "./po-step-labels";

/** What code found, in words a person scans: the kind and its tone. */
const FINDING_LABEL: Record<string, { label: string; tone: StatusTone }> = {
  account_differs: { label: "Tài khoản khác danh mục", tone: "err" },
  account_no_master: { label: "NCC chưa có tài khoản", tone: "warn" },
  account_not_stated: { label: "Không nêu tài khoản", tone: "warn" },
  beneficiary_missing: { label: "Thiếu tài khoản thụ hưởng", tone: "warn" },
  amount_differs: { label: "Số tiền khác", tone: "err" },
  amount_unstated: { label: "Không nêu số tiền", tone: "warn" },
  currency_differs: { label: "Tiền tệ khác", tone: "err" },
  total_differs: { label: "Số khác hệ thống tính", tone: "err" },
  term_missing: { label: "Còn thiếu", tone: "warn" },
  deposit_unrecorded: { label: "Chưa ghi tiền cọc", tone: "warn" },
  source_missing: { label: "Thiếu chứng từ", tone: "warn" },
  source_pending: { label: "Đang đọc", tone: "gray" },
  source_unread: { label: "Máy không đọc được", tone: "warn" },
  line_quantity_differs: { label: "Số lượng khác PO", tone: "err" },
  line_price_differs: { label: "Đơn giá khác PO", tone: "err" },
  line_not_on_po: { label: "SKU ngoài PO", tone: "err" },
  line_missing: { label: "Thiếu dòng PO", tone: "warn" },
  line_unmatched: { label: "Dòng không có SKU", tone: "warn" },
  // Steps 13-15 (ai-automation/17).
  etd_late: { label: "ETD muộn hơn hạn giao", tone: "warn" },
  qc_over_aql: { label: "Số lỗi vượt Ac", tone: "err" },
  qc_stated_differs: { label: "Kết luận khác số lỗi", tone: "err" },
  qc_numbers_missing: { label: "Không đọc được số lỗi", tone: "warn" },
  qc_report_fails: { label: "QC không đạt theo số", tone: "err" },
  container_differs: { label: "Container khác chứng từ", tone: "err" },
  customs_missing: { label: "Thiếu hồ sơ hải quan", tone: "warn" },
  customs_optional_missing: { label: "Chưa có C/O", tone: "gray" },
};

const optionLabel = (value: string) => RESULT_OPTION_LABEL[value] ?? value;

function Suggestion({ result }: { result: POStepResult }) {
  if (result.redacted) {
    return <Typography.Text>{HIDDEN_SUGGESTION}</Typography.Text>;
  }
  if (!result.suggestion?.value) {
    return <Typography.Text>AI không đọc được gợi ý.</Typography.Text>;
  }
  return (
    <Tooltip title={result.suggestion.quote || undefined}>
      <Typography.Text>
        {result.kind === "choice"
          ? `AI gợi ý: ${optionLabel(result.suggestion.value)}`
          : `AI đọc được: “${result.suggestion.value}”`}
      </Typography.Text>
    </Tooltip>
  );
}

/**
 * A PO case's current step as code prepared it (tickets ai-automation/15-18):
 * the draft (its fields shown, and edited, in "Bản nháp chứng từ" below,
 * prices hidden for a viewer without the price scope), what code finds now
 * (an amount, currency, line or beneficiary account that does not match: a
 * finding never blocks, a person decides seeing it), and the results a
 * person types, each empty with AI's reading beside it. The person whose
 * duty the step is approves here. Renders nothing for a state no step AI
 * prepares covers.
 */
export function POStepCard({
  caseId,
  onApproved,
}: {
  caseId: string;
  onApproved: () => void;
}) {
  const { message, modal } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:po-case:${caseId}:step-proposal`,
    useCallback(() => apiClient().getPOStepProposal(caseId), [caseId]),
  );
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const proposal: POStepProposal | null = resource.data;

  if (!proposal) {
    return resource.error != null ? (
      <Card title="AI chuẩn bị bước">
        <LoadError error={resource.error} onRetry={resource.reload} />
      </Card>
    ) : (
      <Card title="AI chuẩn bị bước">
        <RegionState kind="loading" compact />
      </Card>
    );
  }
  if (!proposal.step) return null;
  const words = PO_STEP_LABEL[proposal.step];
  // The outcome a person chose (QC's verdict), when the step has one: a
  // result required only for one outcome (a reason for a fail) follows it.
  const chosen = proposal.results
    .filter((r) => r.kind === "choice")
    .map((r) => typed[r.name] ?? "")[0];
  const unfilled = proposal.results.some(
    (r) =>
      (r.required || (r.required_for !== null && r.required_for === chosen)) &&
      !(typed[r.name] ?? "").trim(),
  );
  const lock = !online
    ? OFFLINE_STEP
    : (proposal.blocked_reason ?? (unfilled ? FILL_RESULTS : null));

  const approve = async () => {
    const body = {
      step: proposal.step!,
      draft_id: proposal.draft_id,
      content_sha256: proposal.content_sha256,
      results: Object.fromEntries(
        proposal.results.map((r) => [r.name, (typed[r.name] ?? "").trim()]),
      ),
    };
    setBusy(true);
    setRefusal(null);
    try {
      await apiClient().approvePOStep(caseId, body, attemptKey(body));
      void message.success(`${words.approve}: đã ghi.`);
      setTyped({});
      resource.reload();
      onApproved();
    } catch (caught) {
      setRefusal(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  };

  const confirm = () =>
    modal.confirm({
      title: `${words.approve}?`,
      content: `${words.confirm} Không hoàn tác được từ màn này.`,
      okText: words.approve,
      okButtonProps: { danger: true },
      autoFocusButton: "cancel",
      cancelText: "Hủy",
      onOk: approve,
    });

  return (
    <Card title={words.title}>
      <Flex vertical gap="small">
        <Flex gap="small" align="center" wrap>
          {proposal.proposed ? (
            <StatusTag tone="ok">AI đề xuất</StatusTag>
          ) : (
            <StatusTag tone="warn">AI chưa đề xuất</StatusTag>
          )}
          <Typography.Text>
            {proposal.proposed
              ? "Mọi điều hệ thống kiểm ở bước này đều khớp."
              : `Còn ${proposal.findings.length} điều cần người kiểm trước khi duyệt.`}
          </Typography.Text>
        </Flex>
        {proposal.draft_id && proposal.draft_doc_type === "rework_request" ? (
          <Typography.Text>
            AI soạn phiếu yêu cầu sửa hàng vì số lỗi vượt giới hạn; xem và sửa ở
            “Bản nháp chứng từ” bên dưới (phiên bản {proposal.draft_version}).
            Phiếu chỉ thành chứng từ khi QC chọn “Không đạt”.
          </Typography.Text>
        ) : proposal.draft_id ? (
          <Typography.Text>
            Số tiền do hệ thống tính; xem và sửa các ô ở “Bản nháp chứng từ” bên
            dưới (phiên bản {proposal.draft_version}).
          </Typography.Text>
        ) : null}
        {proposal.findings.length > 0 ? (
          <List
            size="small"
            aria-label="Điều hệ thống tìm thấy ở bước này"
            dataSource={proposal.findings}
            renderItem={(finding) => {
              const shown = FINDING_LABEL[finding.code] ?? {
                label: finding.code,
                tone: "gray" as StatusTone,
              };
              return (
                <List.Item>
                  <Flex gap="small" align="center" wrap>
                    <StatusTag tone={shown.tone}>{shown.label}</StatusTag>
                    <Typography.Text>{finding.message}</Typography.Text>
                  </Flex>
                </List.Item>
              );
            }}
          />
        ) : null}
        {proposal.missing_paper ? (
          <Typography.Text>
            Cần{" "}
            {DOC_TYPE_LABEL[proposal.missing_paper as DocumentType] ??
              proposal.missing_paper}{" "}
            trên hồ sơ trước khi duyệt.
          </Typography.Text>
        ) : null}
        {proposal.results.map((result) => (
          <Flex key={result.name} gap="small" align="center" wrap>
            {result.kind === "choice" ? (
              <Select
                aria-label={result.label}
                placeholder={result.label}
                options={result.options.map((value) => ({
                  value,
                  label: optionLabel(value),
                }))}
                value={typed[result.name] || undefined}
                onChange={(value: string) =>
                  setTyped((held) => ({ ...held, [result.name]: value }))
                }
                style={{ minWidth: 220 }}
                disabled={!proposal.can_approve}
              />
            ) : (
              <Input
                aria-label={result.label}
                maxLength={result.kind === "text" ? 100 : undefined}
                placeholder={
                  result.kind === "date" ? "YYYY-MM-DD" : result.label
                }
                type={result.kind === "date" ? "date" : "text"}
                inputMode={result.kind === "amount" ? "decimal" : undefined}
                value={typed[result.name] ?? ""}
                onChange={(event) =>
                  setTyped((held) => ({
                    ...held,
                    [result.name]: event.target.value,
                  }))
                }
                style={{ maxWidth: result.kind === "text" ? 420 : 220 }}
                disabled={!proposal.can_approve}
              />
            )}
            <Suggestion result={result} />
          </Flex>
        ))}
        <Flex gap="small" align="center" wrap>
          <Tooltip title={lock}>
            <Button
              type="primary"
              disabled={lock !== null}
              loading={busy}
              onClick={confirm}
            >
              {words.approve}
            </Button>
          </Tooltip>
        </Flex>
        {lock ? <Typography.Text>{lock}</Typography.Text> : null}
        {refusal ? <Alert type="error" showIcon message={refusal} /> : null}
      </Flex>
    </Card>
  );
}
