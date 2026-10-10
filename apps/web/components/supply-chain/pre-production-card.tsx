"use client";

import { useCallback, useState } from "react";
import { Alert, App, Card, Flex, Typography } from "antd";
import { RegionState, StatusTag } from "@dw/ui";
import type { PackagingAction, SampleChecklistRow } from "@dw/api-client";
import { LoadError } from "../load-error";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { ChecklistTable } from "./sample-checklist-card";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const PRE_PRODUCTION_CLOSED =
  "Nhập số đo khi đã nhận mẫu trước SX và test chưa đạt.";
export const PRE_PRODUCTION_NO_DUTY =
  "Nhập số đo cần nhiệm vụ test trước SX (R&D).";

/** Code's suggestion beside the empty choice, in words (ui-quality §7). */
export const TEST_SUGGESTION_LABEL: Partial<Record<PackagingAction, string>> = {
  pass_pre_production_test: "Đạt",
  fail_pre_production_test: "Không đạt",
};

/**
 * Bước 12, test trước SX (ticket ai-automation/17, mục 2): R&D nhập số đo mẫu
 * trước SX theo tiêu chí của nhóm sản phẩm (cùng tiêu chí với vòng mẫu); hệ
 * thống so với chuẩn. Khi đủ số đo, AI soạn biên bản (bảng của hệ thống, ghi
 * chú có dẫn chứng) ở mục Bản nháp; R&D chọn Đạt / Không đạt với biên bản đó.
 * Gợi ý chỉ hiện bên cạnh, không chọn sẵn.
 */
export function PreProductionCard({ caseId }: { caseId: string }) {
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:po-case:${caseId}:pre-production-checklist`,
    useCallback(() => apiClient().getPreProductionChecklist(caseId), [caseId]),
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const checklist = resource.data;

  if (!checklist) {
    return (
      <Card title="Số đo test trước SX">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : (
          <RegionState kind="loading" compact />
        )}
      </Card>
    );
  }

  const locked = !checklist.open
    ? PRE_PRODUCTION_CLOSED
    : !checklist.can_record
      ? PRE_PRODUCTION_NO_DUTY
      : !online
        ? OFFLINE
        : null;
  const suggestion = checklist.suggestion
    ? TEST_SUGGESTION_LABEL[checklist.suggestion]
    : undefined;

  const save = async (row: SampleChecklistRow, value: string) => {
    setBusy(row.key);
    setRefusal(null);
    try {
      await apiClient().recordPreProductionMeasurement(
        caseId,
        { criterion: row.key, value, note: null },
        attemptKey({ key: row.key, value }),
      );
      void message.success(`Đã lưu ${row.label}`);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title={`Số đo test trước SX (lần ${checklist.attempt})`}>
      <Flex vertical gap="small">
        {refusal && <Alert type="error" showIcon title={refusal} />}
        {locked && <Typography.Text>{locked}</Typography.Text>}
        <Flex gap="small" align="center" wrap>
          <Typography.Text>Gợi ý của hệ thống theo số đo:</Typography.Text>
          {suggestion ? (
            <StatusTag tone={suggestion === "Đạt" ? "ok" : "err"}>
              {suggestion}
            </StatusTag>
          ) : (
            <Typography.Text type="secondary">
              chưa đủ số đo để gợi ý
            </Typography.Text>
          )}
        </Flex>
        <ChecklistTable
          rows={checklist.rows}
          locked={locked}
          busy={busy}
          onSave={(row, value) => void save(row, value)}
        />
      </Flex>
    </Card>
  );
}
