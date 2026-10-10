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
  Tooltip,
  Typography,
} from "antd";
import { RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type { PurchaseOrderProposal } from "@dw/api-client";
import { LoadError } from "../load-error";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { OFFLINE_APPROVE, NO_PO_NUMBER } from "./purchase-order-labels";

/** What code found, in words a person scans: the kind and its tone. */
const FINDING_LABEL: Record<string, { label: string; tone: StatusTone }> = {
  term_missing: { label: "Còn thiếu", tone: "warn" },
  term_conflict: { label: "Mâu thuẫn", tone: "err" },
  total_differs: { label: "Tổng khác", tone: "err" },
  bm04_missing: { label: "Thiếu BM04", tone: "warn" },
};

/**
 * Step 10 prepared by code (ticket ai-automation/14): the PO draft's state and
 * what code finds in it now (each term or line cell missing, a total that is
 * not code's, a term the supplier's confirmation states differently from the
 * BM04). Its fields are shown, and edited, in "Bản nháp chứng từ" below,
 * prices hidden for a viewer without the price scope. Cung ứng types the PO
 * number and approves: the PO is created, the draft becomes its document and
 * the case's terms and prices are set, together; Kế toán is told. No price
 * appears on this card.
 */
export function PurchaseOrderCard({
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
    `supply-chain:po-case:${caseId}:purchase-order`,
    useCallback(() => apiClient().getPurchaseOrderProposal(caseId), [caseId]),
  );
  const [reference, setReference] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const proposal: PurchaseOrderProposal | null = resource.data;

  if (!proposal) {
    return (
      <Card title="PO nháp (AI soạn)">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} />
        ) : (
          <RegionState kind="loading" compact />
        )}
      </Card>
    );
  }

  const trimmed = reference.trim();
  const lock = !online
    ? OFFLINE_APPROVE
    : (proposal.blocked_reason ?? (trimmed ? null : NO_PO_NUMBER));

  const approve = async () => {
    if (!proposal.draft_id || !proposal.content_sha256) return;
    const body = {
      draft_id: proposal.draft_id,
      content_sha256: proposal.content_sha256,
      po_reference: trimmed,
    };
    setBusy(true);
    setRefusal(null);
    try {
      await apiClient().approvePurchaseOrder(caseId, body, attemptKey(body));
      void message.success(`Đã tạo PO ${trimmed}; Kế toán đã được báo.`);
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
      title: `Tạo PO ${trimmed} từ bản nháp này?`,
      content:
        "Đơn đặt hàng thành chứng từ của hồ sơ, điều khoản và đơn giá được ghi vào hồ sơ, hồ sơ sang bước PO đã tạo. Không hoàn tác được từ màn này.",
      okText: "Duyệt PO",
      okButtonProps: { danger: true },
      autoFocusButton: "cancel",
      cancelText: "Hủy",
      onOk: approve,
    });

  return (
    <Card title="PO nháp (AI soạn)">
      {!proposal.draft_id ? (
        <Typography.Text>
          Chưa có PO nháp: hệ thống soạn khi hồ sơ chờ tạo PO và công ty bật
          bước này. Vẫn tạo PO tay được ở thẻ bên dưới.
        </Typography.Text>
      ) : (
        <Flex vertical gap="small">
          <Typography.Text>
            Thành tiền, tổng và tiền cọc do hệ thống tính; xem và sửa các ô ở
            “Bản nháp chứng từ” bên dưới (phiên bản {proposal.draft_version}).
          </Typography.Text>
          {proposal.findings.length > 0 ? (
            <List
              size="small"
              aria-label="Điều hệ thống tìm thấy trong PO nháp"
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
          ) : (
            <Typography.Text>
              Hệ thống không thấy ô nào còn thiếu.
            </Typography.Text>
          )}
          <Flex gap="small" align="center" wrap>
            <Input
              aria-label="Số PO"
              placeholder="Ví dụ: PO-2026-0101"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              style={{ maxWidth: 240 }}
              disabled={!proposal.can_approve}
            />
            <Tooltip title={lock}>
              <Button
                type="primary"
                disabled={lock !== null}
                loading={busy}
                onClick={confirm}
              >
                Duyệt PO
              </Button>
            </Tooltip>
          </Flex>
          {lock ? <Typography.Text>{lock}</Typography.Text> : null}
          {refusal ? <Alert type="error" showIcon message={refusal} /> : null}
        </Flex>
      )}
    </Card>
  );
}
