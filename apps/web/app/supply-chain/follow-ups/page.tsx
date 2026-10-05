"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ListTodo, Loader2 } from "lucide-react";
import type { FollowUp, FollowUpKind } from "@dw/contracts";
import { Badge, Button, Card, CardContent, Input, Skeleton } from "@dw/ui";
import { ApiError } from "@dw/api-client";
import { EmptyState } from "../../../components/empty-state";
import { PageHeading } from "../../../components/page-heading";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

const KIND_LABEL: Record<FollowUpKind, string> = {
  update_reminder: "Nhắc NCC",
  update_escalation: "Leo thang",
  sla_breach: "Trễ SLA",
};

const KIND_VARIANT: Record<FollowUpKind, "warning" | "destructive"> = {
  update_reminder: "warning",
  update_escalation: "destructive",
  sla_breach: "destructive",
};

const MILESTONE_LABEL: Record<string, string> = {
  deposit: "đặt cọc",
  port_arrival: "về cảng",
  payment: "thanh toán",
  warehouse_receipt: "nhập kho",
};

function what(item: FollowUp): string {
  if (item.kind === "sla_breach") {
    const milestone = MILESTONE_LABEL[item.milestone ?? ""] ?? item.milestone;
    return `${item.days} ngày ở bước ${milestone}, hạn ${item.limit_days} ngày`;
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
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeading
        icon={ListTodo}
        title="Việc cần làm"
        description="Nhắc NCC, leo thang và trễ SLA đã tới hạn. Việc tự đóng khi tín hiệu hết (NCC gửi cập nhật, case qua bước)."
      />
      <div className="flex gap-2">
        <Button
          variant={showAll ? "outline" : "default"}
          onClick={() => setShowAll(false)}
        >
          Của tôi ({mine.length})
        </Button>
        <Button
          variant={showAll ? "default" : "outline"}
          onClick={() => setShowAll(true)}
        >
          Tất cả ({items.length})
        </Button>
      </div>
      {loading ? (
        <Skeleton className="h-64 w-full" />
      ) : error != null || !data ? (
        <p className="text-sm text-destructive">
          Không tải được việc cần làm:{" "}
          {error instanceof Error ? error.message : "lỗi không xác định"}
        </p>
      ) : shown.length === 0 ? (
        <EmptyState
          icon={CheckCircle2}
          title="Không có việc nào"
          description={
            showAll
              ? "Không case nào đang cần nhắc, leo thang hay trễ SLA."
              : "Không có việc nào giao cho bạn."
          }
        />
      ) : (
        <ul className="space-y-3">
          {shown.map((item) => (
            <li key={item.id}>
              <FollowUpCard item={item} onClosed={reload} />
            </li>
          ))}
        </ul>
      )}
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
      setError(e instanceof ApiError ? e.body.message : "Không đóng được việc");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-2 pt-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={KIND_VARIANT[item.kind]}>
            {KIND_LABEL[item.kind]}
          </Badge>
          <Link
            href={`/supply-chain/po-cases/${item.po_case_id}`}
            className="font-medium hover:underline"
          >
            {item.po_reference}
          </Link>
          <span className="text-muted-foreground">{what(item)}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Mở lúc {new Date(item.opened_at).toLocaleString()}
          {item.notified_at === null && " · chưa ai nhận được thông báo"}
        </p>
        {item.mine && (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Ghi chú (không bắt buộc)"
              className="max-w-sm"
              aria-label={`Ghi chú cho ${item.po_reference}`}
            />
            <Button onClick={() => void close()} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              Đã xử lý
            </Button>
          </div>
        )}
        {error && <p className="text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
