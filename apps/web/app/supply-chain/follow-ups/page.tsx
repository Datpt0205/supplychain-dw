"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import {
  Alert,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Segmented,
  Skeleton,
  Typography,
} from "antd";
import type { FollowUp, FollowUpKind } from "@dw/contracts";
import { PageHeader, RegionState, StatusTag, type StatusTone } from "@dw/ui";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import {
  PoReferenceText,
  poReferenceLabel,
} from "../../../components/supply-chain/po-reference";
import { milestoneLabel } from "../../../components/supply-chain/sla-status-badge";
import { formatDateTimeFull } from "../../../lib/dates";
import { errorMessage, regionFailure } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

const KIND: Record<FollowUpKind, { label: string; tone: StatusTone }> = {
  update_reminder: { label: "Nhắc NCC", tone: "warn" },
  update_escalation: { label: "Leo thang", tone: "err" },
  sla_breach: { label: "Trễ SLA", tone: "err" },
};

function what(item: FollowUp): string {
  if (item.kind === "sla_breach") {
    return `${item.days} ngày ở bước ${milestoneLabel(item.milestone)}, hạn ${item.limit_days} ngày`;
  }
  return `${item.supplier_name} im lặng ${item.days} ngày`;
}

/**
 * Việc cần làm: the follow-ups the sweep opened for due reminders,
 * escalations and SLA breaches. The caller's own come first; only someone a
 * follow-up was handed to can close it (the server refuses anyone else).
 * A follow-up also closes by itself once its signal clears.
 */
export default function FollowUpsPage() {
  const { data, loading, error, reload } = useCachedResource(
    "supply-chain:follow-ups",
    useCallback(() => apiClient().listFollowUps(), []),
  );
  const [showAll, setShowAll] = useState(false);

  const items = data ?? [];
  const mine = items.filter((item) => item.mine);
  const shown = showAll ? items : mine;

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Việc cần làm")}
        title="Việc cần làm"
        description="Nhắc NCC, leo thang và trễ SLA đã tới hạn. Việc tự đóng khi tín hiệu hết (NCC gửi cập nhật, hồ sơ qua bước)."
      />
      <Flex vertical gap="middle">
        <Segmented<"mine" | "all">
          aria-label="Việc của ai"
          value={showAll ? "all" : "mine"}
          options={[
            { value: "mine", label: `Của tôi (${mine.length})` },
            { value: "all", label: `Tất cả (${items.length})` },
          ]}
          onChange={(value) => setShowAll(value === "all")}
        />
        {loading && !data ? (
          <Skeleton active paragraph={{ rows: 6 }} />
        ) : error != null || !data ? (
          // A failed load is never "nothing to do".
          <RegionState
            failure={regionFailure(error)}
            what="việc cần làm"
            onRetry={reload}
          />
        ) : shown.length === 0 ? (
          <Empty
            description={
              <Flex vertical>
                <Typography.Text strong>Không có việc nào</Typography.Text>
                <Typography.Text>
                  {showAll
                    ? "Không Hồ sơ PO nào đang cần nhắc, leo thang hay trễ SLA."
                    : "Không có việc nào giao cho bạn."}
                </Typography.Text>
              </Flex>
            }
          />
        ) : (
          <Flex vertical gap="small" role="list">
            {shown.map((item) => (
              <div key={item.id} role="listitem">
                <FollowUpCard item={item} onClosed={reload} />
              </div>
            ))}
          </Flex>
        )}
      </Flex>
    </div>
  );
}

function FollowUpCard({
  item,
  onClosed,
}: {
  item: FollowUp;
  onClosed: () => void;
}) {
  const online = useOnline();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = async () => {
    setBusy(true);
    setError(null);
    try {
      await apiClient().closeFollowUp(item.id, note);
      onClosed();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card size="small">
      <Flex vertical gap="small">
        <Flex wrap gap="small" align="center">
          <StatusTag tone={KIND[item.kind].tone}>
            {KIND[item.kind].label}
          </StatusTag>
          <Link href={`/supply-chain/po-cases/${item.po_case_id}`}>
            <PoReferenceText reference={item.po_reference} />
          </Link>
          <Typography.Text>{what(item)}</Typography.Text>
        </Flex>
        <Typography.Text type="secondary">
          Mở lúc {formatDateTimeFull(item.opened_at)}
          {item.notified_at === null && " · chưa ai nhận được thông báo"}
        </Typography.Text>
        {item.mine && (
          <Flex wrap gap="small" align="center">
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Ví dụ: đã gọi NCC, hẹn gửi lịch xuất hàng"
              className="max-w-sm"
              aria-label={`Ghi chú cho ${poReferenceLabel(item.po_reference)}`}
            />
            <Button
              type="primary"
              loading={busy}
              disabled={!online}
              onClick={() => void close()}
            >
              Đã xử lý
            </Button>
            {!online && (
              <Typography.Text>
                Không có kết nối mạng. Kết nối lại rồi thử lại.
              </Typography.Text>
            )}
          </Flex>
        )}
        {error && <Alert type="error" showIcon title={error} />}
      </Flex>
    </Card>
  );
}
