"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  Select,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type { SampleChecklistRow, SampleVerdict } from "@dw/api-client";
import { LoadError } from "../load-error";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NO_RECORD =
  "Nhập số đo cần quyền test mẫu (R&D) khi hồ sơ đang ở bước test mẫu.";

/** One table of words for a criterion's verdict (ui-quality §7): code's
 * comparison of R&D's value with the standard, never a model's. */
export const VERDICT_LABEL: Record<SampleVerdict, [string, StatusTone]> = {
  pass: ["Đạt", "ok"],
  fail: ["Không đạt", "err"],
  unmeasured: ["Chưa đo", "unk"],
};

function Entry({
  row,
  locked,
  onSave,
  busy,
}: {
  row: SampleChecklistRow;
  locked: string | null;
  onSave: (value: string) => void;
  busy: boolean;
}) {
  const [value, setValue] = useState<string>("");
  const field =
    row.kind === "check" ? (
      <Select
        aria-label={`Kết quả ${row.label}`}
        className="min-w-32"
        placeholder="Chọn"
        disabled={locked !== null}
        value={value || undefined}
        onChange={(v: string) => setValue(v)}
        options={[
          { value: "pass", label: "Đạt" },
          { value: "fail", label: "Không đạt" },
        ]}
      />
    ) : (
      <Input
        aria-label={`Số đo ${row.label}`}
        className="max-w-40"
        inputMode="decimal"
        disabled={locked !== null}
        value={value}
        suffix={row.unit ?? undefined}
        onChange={(e) => setValue(e.target.value)}
      />
    );
  return (
    <Flex gap="small" align="center" wrap>
      {field}
      <Tooltip title={locked ?? undefined}>
        <Button
          disabled={locked !== null || !value.trim()}
          loading={busy}
          onClick={() => onSave(value.trim())}
        >
          Lưu
        </Button>
      </Tooltip>
    </Flex>
  );
}

/**
 * The criteria table both checklists share (a sample round's, ticket
 * ai-automation/09; the pre-production test's, ticket ai-automation/17): the
 * standard, the value entered, code's verdict and a field to enter a new one.
 */
export function ChecklistTable({
  rows,
  locked,
  busy,
  onSave,
}: {
  rows: SampleChecklistRow[];
  locked: string | null;
  busy: string | null;
  onSave: (row: SampleChecklistRow, value: string) => void;
}) {
  const columns: TableColumnsType<SampleChecklistRow> = [
    { title: "Tiêu chí", key: "label", render: (_, row) => row.label },
    { title: "Chuẩn", key: "standard", render: (_, row) => row.standard },
    {
      title: "Đã nhập",
      key: "value",
      render: (_, row) =>
        row.value === null
          ? null
          : row.kind === "check"
            ? VERDICT_LABEL[row.value === "pass" ? "pass" : "fail"][0]
            : `${row.value}${row.unit ? ` ${row.unit}` : ""}`,
    },
    {
      title: "Kết quả",
      key: "verdict",
      render: (_, row) => {
        const [label, tone] = VERDICT_LABEL[row.verdict];
        return <StatusTag tone={tone}>{label}</StatusTag>;
      },
    },
    {
      title: "Nhập mới",
      key: "entry",
      render: (_, row) => (
        <Entry
          row={row}
          locked={locked}
          busy={busy === row.key}
          onSave={(value) => onSave(row, value)}
        />
      ),
    },
  ];
  return (
    <Table<SampleChecklistRow>
      rowKey="key"
      size="small"
      columns={columns}
      dataSource={rows}
      pagination={false}
      scroll={{ x: "max-content" }}
    />
  );
}

/**
 * Bước 3-5 (ticket ai-automation/09): R&D nhập số đo theo từng tiêu chí của
 * nhóm sản phẩm; hệ thống so với chuẩn và hiện Đạt / Không đạt / Chưa đo. Biên
 * bản, phiếu chỉnh sửa và gợi ý kết luận AI chuẩn bị đều đọc đúng bảng này; số
 * nhập sau khi AI đã trình làm đề xuất cũ đi và AI chuẩn bị lại.
 */
export function SampleChecklistCard({ caseId }: { caseId: string }) {
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:product-case:${caseId}:sample-checklist`,
    useCallback(() => apiClient().getSampleChecklist(caseId), [caseId]),
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const checklist = resource.data;

  if (!checklist) {
    return (
      <Card title="Số đo vòng mẫu">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : (
          <RegionState kind="loading" compact />
        )}
      </Card>
    );
  }

  const locked = !checklist.can_record ? NO_RECORD : !online ? OFFLINE : null;

  const save = async (row: SampleChecklistRow, value: string) => {
    setBusy(row.key);
    setRefusal(null);
    try {
      await apiClient().recordSampleMeasurement(
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
    <Card title={`Số đo vòng mẫu ${checklist.sample_round}`}>
      <Flex vertical gap="small">
        {refusal && <Alert type="error" showIcon title={refusal} />}
        {locked && <Typography.Text>{locked}</Typography.Text>}
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
