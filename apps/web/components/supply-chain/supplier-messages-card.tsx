"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Tooltip,
  Typography,
} from "antd";
import { RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type {
  SupplierMessage,
  SupplierMessagePurpose,
  SupplierMessageStatus,
} from "@dw/api-client";
import { LoadError } from "../load-error";
import { formatDateTimeFull } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NOTHING_TO_SEND =
  "AI không soạn được thư này; hãy tự viết từ hộp thư của bạn.";
export const ALREADY_SENT = "Thư này đã được đánh dấu đã gửi.";

/** One table of words for a message's purpose and status (ui-quality §7). */
export const MESSAGE_PURPOSE_LABEL: Record<SupplierMessagePurpose, string> = {
  sample_request: "Đề nghị gửi mẫu",
  supplier_reminder: "Nhắc NCC cập nhật",
  supplier_confirmation: "Xác nhận sản phẩm",
  sample_revision_request: "Gửi phiếu yêu cầu chỉnh sửa mẫu",
};
export const MESSAGE_STATUS_LABEL: Record<
  SupplierMessageStatus,
  [string, StatusTone]
> = {
  drafted: ["AI đã soạn", "geek"],
  refused: ["AI không soạn được", "unk"],
  failed: ["AI không soạn được", "unk"],
};

/** `mailto:` with the subject and body; the person's own mail app sends it. */
export function mailtoOf(message: SupplierMessage): string {
  const query = new URLSearchParams({
    subject: message.subject,
    body: message.body,
  })
    .toString()
    .replace(/\+/g, "%20");
  return `mailto:${encodeURIComponent(message.recipient_email ?? "")}?${query}`;
}

function lockOf(message: SupplierMessage, online: boolean): string | null {
  if (message.status !== "drafted") return NOTHING_TO_SEND;
  if (message.sent_at !== null) return ALREADY_SENT;
  if (!online) return OFFLINE;
  return null;
}

/**
 * Messages to the case's supplier (ADR 0029, E18): AI drafts each one, a
 * person reads it, copies it (or opens it in their mail app) and sends it from
 * their own mailbox, then presses "Đã gửi". Nothing here sends anything; the
 * app records who said it was sent, and which text.
 */
export function SupplierMessagesCard({
  caseKind,
  caseId,
}: {
  caseKind: "po" | "product";
  caseId: string;
}) {
  const { message: toast } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:${caseKind}-case:${caseId}:supplier-messages`,
    useCallback(
      () => apiClient().listSupplierMessages(caseKind, caseId),
      [caseKind, caseId],
    ),
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const messages = resource.data;

  if (!messages) {
    return (
      <Card title="Thư gửi NCC">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }

  const copy = async (m: SupplierMessage) => {
    try {
      await navigator.clipboard.writeText(`${m.subject}\n\n${m.body}`);
      void toast.success("Đã sao chép tiêu đề và nội dung thư");
    } catch {
      void toast.error("Không sao chép được; hãy chọn và sao chép bằng tay.");
    }
  };

  const markSent = async (m: SupplierMessage) => {
    setBusy(m.id);
    setRefusal(null);
    try {
      await apiClient().markSupplierMessageSent(
        m.id,
        m.content_sha256,
        attemptKey({ id: m.id, sha: m.content_sha256 }),
      );
      void toast.success("Đã ghi nhận thư đã gửi");
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title="Thư gửi NCC">
      {messages.length === 0 ? (
        <Empty description="Chưa có thư nào. AI soạn thư khi hồ sơ cần liên hệ NCC; bạn kiểm, sao chép và tự gửi." />
      ) : (
        <Flex vertical gap="large">
          {refusal && <Alert type="error" showIcon title={refusal} />}
          {messages.map((m) => {
            const [label, tone] = MESSAGE_STATUS_LABEL[m.status];
            const lock = lockOf(m, online);
            return (
              <Flex key={m.id} vertical gap="small">
                <Flex justify="space-between" align="center" wrap gap="small">
                  <Flex gap="small" align="center" wrap>
                    <Typography.Text strong>
                      {MESSAGE_PURPOSE_LABEL[m.purpose]}
                    </Typography.Text>
                    <StatusTag tone={tone}>{label}</StatusTag>
                    {m.sent_at && (
                      <StatusTag tone="ok">
                        {`Đã gửi ${formatDateTimeFull(m.sent_at)}`}
                      </StatusTag>
                    )}
                  </Flex>
                  {m.status === "drafted" && (
                    <Flex gap="small" align="center" wrap>
                      <Button onClick={() => void copy(m)}>Sao chép</Button>
                      <Button href={mailtoOf(m)}>Mở trong ứng dụng thư</Button>
                      <Tooltip title={lock ?? undefined}>
                        <Button
                          type="primary"
                          disabled={lock !== null}
                          loading={busy === m.id}
                          onClick={() => void markSent(m)}
                        >
                          Đã gửi
                        </Button>
                      </Tooltip>
                    </Flex>
                  )}
                </Flex>
                <Typography.Text>
                  Gửi tới:{" "}
                  {m.recipient_name ?? m.supplier_name ?? "Chưa có người nhận"}
                  {m.recipient_email ? ` <${m.recipient_email}>` : ""}
                </Typography.Text>
                <Typography.Text strong>{m.subject}</Typography.Text>
                {m.status === "drafted" ? (
                  <Typography.Paragraph className="whitespace-pre-wrap">
                    {m.body}
                  </Typography.Paragraph>
                ) : (
                  <Typography.Text>{NOTHING_TO_SEND}</Typography.Text>
                )}
                {m.dropped > 0 && (
                  <Typography.Text type="secondary">
                    {`AI viết thêm ${m.dropped} đoạn không kiểm chứng được nên đã bỏ.`}
                  </Typography.Text>
                )}
                <Typography.Text type="secondary">
                  Soạn lúc {formatDateTimeFull(m.created_at)}
                </Typography.Text>
              </Flex>
            );
          })}
        </Flex>
      )}
    </Card>
  );
}
