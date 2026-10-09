"use client";

import { useCallback } from "react";
import { Alert, Card, Descriptions, Flex, Typography } from "antd";
import { MaskedValue, RegionState, StatusTag } from "@dw/ui";
import type { DraftField } from "@dw/api-client";
import { LoadError } from "../load-error";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";

/** BGĐ's review of a passed sample: the approval type that carries one. */
export const BOD_REVIEW_APPROVAL_TYPE =
  "supply_chain.product_action.bod_review";
export const NO_SUBMISSION =
  "Chưa có tờ trình AI soạn cho lần trình này; BGĐ quyết theo hồ sơ và biên bản.";

/** The draft an approval's payload names, or null. */
export function submissionDraftId(
  payload: Record<string, unknown>,
): string | null {
  const raw = payload["bod_submission"];
  if (raw && typeof raw === "object" && "draft_id" in raw) {
    const id = (raw as { draft_id?: unknown }).draft_id;
    return typeof id === "string" ? id : null;
  }
  return null;
}

function Value({ field }: { field: DraftField }) {
  if (field.redacted) return <MaskedValue />;
  if (field.value === null)
    return field.gap ? <StatusTag tone="unk">Thiếu</StatusTag> : null;
  return (
    <Flex vertical gap={2}>
      <Typography.Text className="whitespace-pre-wrap">
        {field.value}
      </Typography.Text>
      {field.source?.ai_written && (
        <Typography.Text>AI viết, đã kiểm dẫn chứng</Typography.Text>
      )}
      {field.source?.quote && (
        <Typography.Text>Máy đọc: “{field.source.quote}”</Typography.Text>
      )}
    </Flex>
  );
}

function Submission({ draftId }: { draftId: string }) {
  const resource = useCachedResource(
    `supply-chain:draft:${draftId}`,
    useCallback(() => apiClient().getDraft(draftId), [draftId]),
  );
  const draft = resource.data;
  if (!draft) {
    return resource.error != null ? (
      <LoadError error={resource.error} onRetry={resource.reload} compact />
    ) : (
      <RegionState kind="loading" compact />
    );
  }
  return (
    <Flex vertical gap="small">
      {!draft.prices_visible && (
        <Alert
          type="info"
          showIcon
          title="Giá chỉ hiện với người có quyền xem dữ liệu thương mại."
        />
      )}
      <Descriptions
        size="small"
        bordered
        column={1}
        items={draft.fields
          .filter((f) => f.kind !== "table")
          .map((f) => ({
            key: f.name,
            label: f.label,
            children: <Value field={f} />,
          }))}
      />
    </Flex>
  );
}

/**
 * Tờ trình BGĐ (ticket ai-automation/10) on BGĐ's review: the draft AI
 * prepared before the review was raised, read as the viewer may read it (a
 * price redacted without the commercial scope), every AI-written sentence
 * marked as such. No tờ trình never blocks the decision.
 */
export function BodSubmissionPanel({
  payload,
}: {
  payload: Record<string, unknown>;
}) {
  const draftId = submissionDraftId(payload);
  return (
    <Card size="small" title="Tờ trình BGĐ (AI soạn)">
      {draftId ? (
        <Submission draftId={draftId} />
      ) : (
        <Typography.Text>{NO_SUBMISSION}</Typography.Text>
      )}
    </Card>
  );
}
