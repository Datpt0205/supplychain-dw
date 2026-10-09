"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { MaskedValue, RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type { DocumentDraft, DraftField, DraftStatus } from "@dw/api-client";
import { LoadError } from "../load-error";
import { formatDateTimeFull } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { saveBlob } from "./case-documents-card";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NO_DRAFT_EDIT =
  "Sửa bản nháp cần quyền ghi chứng từ; vai của bạn chưa có.";
export const DRAFT_CLOSED =
  "Bản nháp này đã đóng; chỉ phiên bản mới nhất còn mở được sửa.";
export const GAP = "Thiếu";

/** One table of words for a draft version's status (ui-quality §7). */
export const DRAFT_STATUS_LABEL: Record<DraftStatus, [string, StatusTone]> = {
  open: ["Chờ người kiểm", "warn"],
  confirmed: ["Đã duyệt thành chứng từ", "ok"],
  rejected: ["Đã từ chối", "err"],
  superseded: ["Đã có bản mới hơn", "gray"],
};

function lockOf(draft: DocumentDraft, online: boolean): string | null {
  if (draft.status !== "open") return DRAFT_CLOSED;
  if (!draft.can_edit) return NO_DRAFT_EDIT;
  if (!online) return OFFLINE;
  return null;
}

function Value({ field }: { field: DraftField }) {
  if (field.redacted) return <MaskedValue />;
  if (field.kind === "table") {
    const rows = field.rows ?? [];
    if (rows.length === 0)
      return field.gap ? <StatusTag tone="unk">{GAP}</StatusTag> : null;
    return (
      <Flex vertical gap={4}>
        {rows.map((row, index) => (
          <Typography.Text key={index}>
            {(field.columns ?? [])
              .map((column) =>
                field.redacted_columns.includes(column.name)
                  ? `${column.label}: Đã ẩn`
                  : `${column.label}: ${row[column.name] ?? GAP}`,
              )
              .join(" · ")}
          </Typography.Text>
        ))}
      </Flex>
    );
  }
  if (field.value === null)
    return field.gap ? <StatusTag tone="unk">{GAP}</StatusTag> : null;
  return <Typography.Text>{field.value}</Typography.Text>;
}

function Source({ field }: { field: DraftField }) {
  const source = field.source;
  if (source === null) return null;
  if (source.edited_by) return <Typography.Text>Người sửa</Typography.Text>;
  if (source.quote)
    return (
      <Tooltip title={source.quote}>
        <Typography.Text>Máy đọc: “{source.quote}”</Typography.Text>
      </Tooltip>
    );
  return null;
}

/**
 * The drafts of a case (ADR 0025 point 4): each field with its value, a gap
 * marked as such, and where the value came from (a quote the machine read, or
 * the person who typed it). A preview prints the draft by its template; an
 * edit is a new version; a rejection needs a reason. A draft becomes a case
 * document only when a person approves the step it was prepared for.
 */
export function DraftsCard({
  caseKind,
  caseId,
}: {
  caseKind: "po" | "product";
  caseId: string;
}) {
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const resource = useCachedResource(
    `supply-chain:${caseKind}-case:${caseId}:drafts`,
    useCallback(
      () => apiClient().listCaseDrafts(caseKind, caseId),
      [caseKind, caseId],
    ),
  );
  const [editing, setEditing] = useState<DocumentDraft | null>(null);
  const [rejecting, setRejecting] = useState<DocumentDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [form] = Form.useForm<Record<string, string>>();
  const [reasonForm] = Form.useForm<{ reason: string }>();
  const drafts = resource.data;

  if (!drafts) {
    return (
      <Card title="Bản nháp chứng từ">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }

  const preview = async (draft: DocumentDraft) => {
    try {
      saveBlob(
        await apiClient().downloadDraft(draft.id),
        `${draft.title} - ban nhap v${draft.version}.docx`,
      );
    } catch (error) {
      void message.error(errorMessage(error));
    }
  };

  const openEdit = (draft: DocumentDraft) => {
    setRefusal(null);
    form.setFieldsValue(
      Object.fromEntries(
        draft.fields
          .filter((f) => f.kind !== "table" && !f.redacted)
          .map((f) => [f.name, f.value ?? ""]),
      ),
    );
    setEditing(draft);
  };

  const saveEdit = async (values: Record<string, string>) => {
    if (!editing) return;
    const changed = Object.fromEntries(
      editing.fields
        .filter((f) => f.kind !== "table" && !f.redacted)
        .filter((f) => (values[f.name] ?? "") !== (f.value ?? ""))
        .map((f) => [f.name, values[f.name]?.trim() || null]),
    );
    if (Object.keys(changed).length === 0) {
      setEditing(null);
      return;
    }
    setBusy(true);
    try {
      const saved = await apiClient().reviseDraft(
        editing.id,
        changed,
        attemptKey(changed),
      );
      void message.success(`Đã lưu bản nháp phiên bản ${saved.version}`);
      setEditing(null);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const reject = async ({ reason }: { reason: string }) => {
    if (!rejecting) return;
    setBusy(true);
    try {
      await apiClient().rejectDraft(
        rejecting.id,
        reason.trim(),
        attemptKey({ reason }),
      );
      void message.success("Đã từ chối bản nháp");
      setRejecting(null);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const columns: TableColumnsType<DraftField> = [
    {
      title: "Trường",
      key: "label",
      render: (_, f) => (
        <Typography.Text>
          {f.label}
          {f.required ? " *" : ""}
        </Typography.Text>
      ),
    },
    { title: "Giá trị", key: "value", render: (_, f) => <Value field={f} /> },
    { title: "Nguồn", key: "source", render: (_, f) => <Source field={f} /> },
  ];

  return (
    <Card title="Bản nháp chứng từ">
      {drafts.length === 0 ? (
        <Empty description="Chưa có bản nháp. AI soạn bản nháp khi hồ sơ vào bước cần chứng từ; người kiểm và duyệt." />
      ) : (
        <Flex vertical gap="large">
          {drafts.some((d) => !d.prices_visible) && (
            <Alert
              type="info"
              showIcon
              title="Giá chỉ hiện với người có quyền xem dữ liệu thương mại."
            />
          )}
          {drafts.map((draft) => {
            const [label, tone] = DRAFT_STATUS_LABEL[draft.status];
            const lock = lockOf(draft, online);
            return (
              <Flex key={draft.id} vertical gap="small">
                <Flex justify="space-between" align="center" wrap gap="small">
                  <Flex gap="small" align="center" wrap>
                    <Typography.Text strong>{draft.title}</Typography.Text>
                    <Typography.Text
                      code
                    >{`v${draft.version}`}</Typography.Text>
                    <StatusTag tone={tone}>{label}</StatusTag>
                    {draft.prompt_id && (
                      <StatusTag tone="geek">AI soạn</StatusTag>
                    )}
                    {draft.gaps.length > 0 && (
                      <StatusTag tone="unk">{`${draft.gaps.length} ô thiếu`}</StatusTag>
                    )}
                  </Flex>
                  <Flex gap="small" align="center" wrap>
                    <Button onClick={() => void preview(draft)}>
                      Tải bản xem trước
                    </Button>
                    <Tooltip title={lock ?? undefined}>
                      <Button
                        disabled={lock !== null}
                        onClick={() => openEdit(draft)}
                      >
                        Sửa trường
                      </Button>
                    </Tooltip>
                    <Tooltip title={lock ?? undefined}>
                      <Button
                        danger
                        disabled={lock !== null}
                        onClick={() => {
                          setRefusal(null);
                          reasonForm.resetFields();
                          setRejecting(draft);
                        }}
                      >
                        Từ chối
                      </Button>
                    </Tooltip>
                  </Flex>
                </Flex>
                {lock && <Typography.Text>{lock}</Typography.Text>}
                {draft.decision_reason && (
                  <Typography.Text>
                    Lý do từ chối: {draft.decision_reason}
                  </Typography.Text>
                )}
                <Table<DraftField>
                  rowKey="name"
                  size="small"
                  columns={columns}
                  dataSource={draft.fields}
                  pagination={false}
                  scroll={{ x: "max-content" }}
                />
                <Typography.Text type="secondary">
                  Tạo lúc {formatDateTimeFull(draft.created_at)}
                </Typography.Text>
              </Flex>
            );
          })}
        </Flex>
      )}
      <Modal
        open={editing !== null}
        title={editing ? `Sửa ${editing.title}` : ""}
        okText="Lưu phiên bản mới"
        cancelText="Hủy"
        confirmLoading={busy}
        onOk={() => form.submit()}
        onCancel={() => setEditing(null)}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(v) => void saveEdit(v)}>
          {refusal && <Alert type="error" showIcon title={refusal} />}
          {editing?.fields
            .filter((f) => f.kind !== "table" && !f.redacted)
            .map((f) => (
              <Form.Item
                key={f.name}
                name={f.name}
                label={f.label}
                extra={f.kind === "date" ? "Dạng YYYY-MM-DD" : undefined}
              >
                <Input />
              </Form.Item>
            ))}
        </Form>
      </Modal>
      <Modal
        open={rejecting !== null}
        title="Từ chối bản nháp"
        okText="Từ chối"
        okButtonProps={{ danger: true }}
        cancelText="Hủy"
        confirmLoading={busy}
        onOk={() => reasonForm.submit()}
        onCancel={() => setRejecting(null)}
        destroyOnHidden
      >
        <Form
          form={reasonForm}
          layout="vertical"
          onFinish={(v) => void reject(v)}
        >
          {refusal && <Alert type="error" showIcon title={refusal} />}
          <Form.Item
            name="reason"
            label="Lý do"
            rules={[
              {
                required: true,
                whitespace: true,
                message: "Cần lý do từ chối",
              },
            ]}
          >
            <Input.TextArea maxLength={1000} autoSize={{ minRows: 2 }} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
