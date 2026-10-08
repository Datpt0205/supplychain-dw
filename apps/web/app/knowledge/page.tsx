"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  BookOutlined,
  DeleteOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { IngestJob, KnowledgeDocument } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../components/load-error";
import { LoadMore } from "../../components/load-more";
import { useAuth } from "../../lib/auth/auth-context";
import { formatDateTime } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { apiClient } from "../../lib/session";
import { useCachedPages } from "../../lib/use-cached-pages";

// The ingest job is polled rather than pushed: the worker picks it off a queue
// and a parse takes seconds, not milliseconds. Five minutes at this interval is
// long enough for a 50 MB file and short enough to give up honestly.
const POLL_MS = 2_000;
const POLL_ATTEMPTS = 150;

const JOB_STATUS: Record<string, { label: string; color: string }> = {
  queued: { label: "Đang chờ", color: "default" },
  running: { label: "Đang xử lý", color: "processing" },
  done: { label: "Xong", color: "success" },
  failed: { label: "Lỗi", color: "error" },
};

const SCOPE: Record<string, { label: string; color: string }> = {
  tenant: { label: "Trong công ty", color: "default" },
  global: { label: "Mọi công ty", color: "gold" },
};

export default function KnowledgePage() {
  const { hasScope, hasRole } = useAuth();
  const { modal } = App.useApp();
  // Jobs this browser started. The API exposes a job by id, not a list, so the
  // page follows the ones it queued rather than inventing a history it cannot
  // read back after a reload.
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [domain, setDomain] = useState("shared");
  const [scope, setScope] = useState<"tenant" | "global">("tenant");

  const canWrite = hasScope("knowledge.write");
  // Publishing across tenants is a platform-admin act; the API refuses it from
  // anyone else, so the option is offered to exactly the same set.
  const canPublishGlobal = hasRole("platform_admin");

  const {
    items: documents,
    loading,
    loadingMore,
    error: loadError,
    hasMore,
    loadMore,
    reload,
  } = useCachedPages(
    "knowledge:documents",
    useCallback(
      (cursor: string | null) => apiClient().listKnowledgeDocuments({ cursor }),
      [],
    ),
  );

  function trackJob(job: IngestJob) {
    setJobs((current) => [
      job,
      ...current.filter((item) => item.job_id !== job.job_id),
    ]);
  }

  /** Follow one job to a terminal state, refreshing the list when it lands. */
  async function pollJob(jobId: string): Promise<void> {
    for (let attempt = 0; attempt < POLL_ATTEMPTS; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      const job = await apiClient().getIngestJob(jobId);
      trackJob(job);
      if (job.status === "done" || job.status === "failed") {
        reload();
        return;
      }
    }
  }

  async function upload() {
    if (!file || !title.trim()) return;
    setBusy(true);
    try {
      const job = await apiClient().uploadKnowledgeDocument(file, {
        title: title.trim(),
        domain,
        scope,
      });
      trackJob(job);
      setError(null);
      setFormOpen(false);
      setTitle("");
      setFile(null);
      void pollJob(job.job_id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function confirmRemove(document: KnowledgeDocument) {
    modal.confirm({
      title: `Gỡ tài liệu “${document.title}”?`,
      content: "Worker sẽ không truy xuất được tài liệu này nữa.",
      okText: "Gỡ",
      okButtonProps: { danger: true },
      cancelText: "Hủy",
      autoFocusButton: "cancel",
      onOk: async () => {
        try {
          await apiClient().deleteKnowledgeDocument(document.document_id);
          setError(null);
          reload();
        } catch (e) {
          setError(errorMessage(e));
        }
      },
    });
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        icon={<BookOutlined />}
        title="Tri thức"
        subtitle="Tài liệu mà worker truy xuất. Mỗi tài liệu có phạm vi và phiên bản, và việc truy xuất luôn lọc theo công ty của người đọc."
        actions={
          <>
            {canWrite && (
              <Button
                type="primary"
                icon={<UploadOutlined aria-hidden />}
                onClick={() => setFormOpen(true)}
              >
                Thêm tài liệu
              </Button>
            )}
            <Button
              icon={<ReloadOutlined aria-hidden />}
              aria-label="Tải lại"
              onClick={reload}
            />
          </>
        }
      />
      <Flex vertical gap="middle">
        {error != null && <Alert type="error" showIcon title={error} />}
        {loadError != null && <LoadError error={loadError} onRetry={reload} />}
        {loading && loadError == null && <RegionState kind="loading" />}
        {!loading && loadError == null && documents.length === 0 && (
          <RegionState
            kind="empty"
            title="Chưa có tài liệu nào"
            description="Tải lên tài liệu đầu tiên để worker có nguồn truy xuất."
          />
        )}

        {documents.length > 0 && (
          <Card>
            <Table<KnowledgeDocument>
              rowKey="document_id"
              size="small"
              pagination={false}
              scroll={{ x: "max-content" }}
              dataSource={documents}
              columns={[
                { title: "Tiêu đề", dataIndex: "title" },
                { title: "Lĩnh vực", dataIndex: "domain" },
                {
                  title: "Phạm vi",
                  dataIndex: "scope",
                  render: (value: string) => {
                    const shown = SCOPE[value] ?? {
                      label: value,
                      color: "default",
                    };
                    return <Tag color={shown.color}>{shown.label}</Tag>;
                  },
                },
                { title: "Phân loại", dataIndex: "classification" },
                { title: "Số đoạn", dataIndex: "chunk_count", align: "right" },
                {
                  title: "Phiên bản",
                  key: "version",
                  // Source version is what the uploader stamped; index version
                  // is what the retrieval index was built with.
                  render: (_, document) => (
                    <Typography.Text code className="text-xs">
                      {document.source_version}
                      {document.index_version
                        ? ` · ${document.index_version}`
                        : ""}
                    </Typography.Text>
                  ),
                },
                {
                  title: "Thêm lúc",
                  dataIndex: "created_at",
                  render: (value: string) => formatDateTime(value),
                },
                ...(canWrite
                  ? [
                      {
                        title: "",
                        key: "remove",
                        render: (_: unknown, document: KnowledgeDocument) =>
                          document.scope !== "global" || canPublishGlobal ? (
                            <Button
                              type="text"
                              danger
                              icon={<DeleteOutlined aria-hidden />}
                              aria-label={`Gỡ ${document.title}`}
                              onClick={() => confirmRemove(document)}
                            />
                          ) : null,
                      },
                    ]
                  : []),
              ]}
            />
          </Card>
        )}

        {!loading && documents.length > 0 && (
          <LoadMore
            hasMore={hasMore}
            loading={loadingMore}
            onLoadMore={loadMore}
            shown={documents.length}
            noun="tài liệu"
          />
        )}

        {jobs.length > 0 && (
          <Card title="Việc nạp tài liệu">
            <Typography.Paragraph type="secondary">
              Các lần tải lên từ trình duyệt này. Tài liệu chỉ truy xuất được
              khi việc nạp báo xong.
            </Typography.Paragraph>
            <Table<IngestJob>
              rowKey="job_id"
              size="small"
              pagination={false}
              scroll={{ x: "max-content" }}
              dataSource={jobs}
              columns={[
                {
                  title: "Xếp hàng lúc",
                  dataIndex: "created_at",
                  render: (value: string) => formatDateTime(value),
                },
                {
                  title: "Tệp",
                  key: "file",
                  render: (_, job) => (
                    <Flex vertical>
                      <Typography.Text strong>{job.title}</Typography.Text>
                      <Typography.Text type="secondary">
                        {job.filename}
                      </Typography.Text>
                    </Flex>
                  ),
                },
                {
                  title: "Trạng thái",
                  dataIndex: "status",
                  render: (value: string) => {
                    const shown = JOB_STATUS[value] ?? {
                      label: value,
                      color: "default",
                    };
                    return <Tag color={shown.color}>{shown.label}</Tag>;
                  },
                },
                { title: "Số lần thử", dataIndex: "attempts", align: "right" },
                {
                  title: "Số đoạn",
                  dataIndex: "chunk_count",
                  align: "right",
                  render: (value: number | null) => value ?? "—",
                },
                {
                  title: "Ghi chú",
                  key: "notes",
                  width: 260,
                  // A warning means the file was indexed but not read whole —
                  // the uploader is the only person who can act on it, so it
                  // is never swallowed.
                  render: (_, job) =>
                    job.error ??
                    (job.warnings.length > 0 ? job.warnings.join(" · ") : "—"),
                },
              ]}
            />
          </Card>
        )}
      </Flex>

      {canWrite && (
        <Modal
          open={formOpen}
          title="Thêm tài liệu"
          onCancel={() => {
            if (!busy) setFormOpen(false);
          }}
          okText="Tải lên"
          cancelText="Hủy"
          okButtonProps={{
            icon: <UploadOutlined aria-hidden />,
            loading: busy,
            disabled: !file || !title.trim(),
          }}
          cancelButtonProps={{ disabled: busy }}
          onOk={() => void upload()}
          destroyOnHidden
        >
          <Flex vertical gap="middle">
            <Typography.Text type="secondary">
              Tệp được đưa lên kho lưu trữ và worker đọc nó; chưa truy xuất được
              gì tới khi việc nạp báo xong.
            </Typography.Text>
            <Flex vertical gap={4}>
              <span>Tệp</span>
              <Upload
                maxCount={1}
                beforeUpload={(picked) => {
                  setFile(picked);
                  // Kept in the browser; the API call above sends it.
                  return false;
                }}
                onRemove={() => setFile(null)}
              >
                <Button icon={<UploadOutlined aria-hidden />}>Chọn tệp</Button>
              </Upload>
            </Flex>
            <Flex vertical gap={4}>
              <label htmlFor="knowledge-title">Tiêu đề</label>
              <Input
                id="knowledge-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Tên hiện trong danh sách"
              />
            </Flex>
            <div className="grid gap-4 sm:grid-cols-2">
              <Flex vertical gap={4}>
                <label htmlFor="knowledge-domain">Lĩnh vực</label>
                <Input
                  id="knowledge-domain"
                  value={domain}
                  onChange={(event) => setDomain(event.target.value)}
                  placeholder="shared"
                />
              </Flex>
              <Flex vertical gap={4}>
                <label htmlFor="knowledge-scope">Phạm vi</label>
                <Select
                  id="knowledge-scope"
                  value={scope}
                  onChange={setScope}
                  options={[
                    { value: "tenant", label: "Chỉ công ty này" },
                    {
                      value: "global",
                      label: canPublishGlobal
                        ? "Mọi công ty"
                        : "Mọi công ty — chỉ quản trị nền tảng",
                      disabled: !canPublishGlobal,
                    },
                  ]}
                />
              </Flex>
            </div>
          </Flex>
        </Modal>
      )}
    </div>
  );
}
