"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Select,
  Table,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { TableColumnsType, UploadFile } from "antd";
import {
  DownloadOutlined,
  PaperClipOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import type { CaseDocument, DocumentType } from "@dw/api-client";
import { formatDateTime, VN_TIME } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { LoadError } from "../load-error";
import { newIdempotencyKey } from "../../lib/idempotency-key";
import { useOnline } from "../../lib/hooks/use-online";
import { apiClient } from "../../lib/session";

/**
 * The one table of Vietnamese names for `doc_type` (ADR 0021, process.md
 * section 2). Every screen that names a document type reads it from here.
 */
export const DOC_TYPE_LABEL: Record<DocumentType, string> = {
  proposal_list: "Danh sách SP đề xuất",
  product_image: "Ảnh sản phẩm",
  sample_photo: "Ảnh mẫu",
  sample_evaluation: "Biên bản đánh giá mẫu",
  sample_revision_request: "Phiếu yêu cầu chỉnh sửa mẫu",
  product_profile_bm04: "Profile SP (BM04)",
  official_item_code: "Mã hàng hóa chính thức",
  supplier_confirmation_email: "Email xác nhận của NCC",
  purchase_order: "Đơn đặt hàng (PO)",
  deposit_docs: "Hồ sơ đặt cọc",
  payment_docs: "Hồ sơ thanh toán",
  packaging_content: "Nội dung bao bì",
  user_manual: "Sách HDSD",
  maquette: "Maquette",
  colour_sample: "Mẫu màu",
  packaging_design: "Thiết kế bao bì",
  pre_production_test_report: "Biên bản test trước SX",
};

const DOC_TYPE_OPTIONS = (Object.keys(DOC_TYPE_LABEL) as DocumentType[]).map(
  (value) => ({ value, label: DOC_TYPE_LABEL[value] }),
);

/** The case kinds whose documents this card can show: a PO case and a
 * product-development case (stage 1), each with its own two calls. */
export type CaseKind = "po" | "product";

const DOCUMENTS_API: Record<
  CaseKind,
  {
    list: (caseId: string) => Promise<CaseDocument[]>;
    upload: (
      caseId: string,
      input: { docType: DocumentType; file: File; idempotencyKey: string },
    ) => Promise<CaseDocument>;
  }
> = {
  po: {
    list: (caseId) => apiClient().listCaseDocuments(caseId),
    upload: (caseId, input) => apiClient().uploadCaseDocument(caseId, input),
  },
  product: {
    list: (caseId) => apiClient().listProductCaseDocuments(caseId),
    upload: (caseId, input) =>
      apiClient().uploadProductCaseDocument(caseId, input),
  },
};

// What the API accepts; it decides by content, this only narrows the picker.
export const ACCEPT = ".pdf,.jpg,.jpeg,.png,.xlsx,.docx,.eml,.msg";
export const NO_WRITE_REASON =
  "Chỉ người có quyền tải chứng từ lên (vai vận hành Supply Chain) mới thêm được chứng từ.";
const OFFLINE_REASON = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024)
    return `${(bytes / 1024).toFixed(1).replace(".", ",")} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

type ListState =
  | { kind: "loading" }
  | { kind: "error"; error: unknown }
  | { kind: "ready"; documents: CaseDocument[] };

/** One press's identity: a retry of the same type and file reuses its key. */
interface Attempt {
  key: string;
  docType: DocumentType;
  file: File;
}

/**
 * Uploading one document to one case: the one place a press becomes an
 * `Idempotency-Key` (reused on a retry of the same type and file, replaced
 * after either changes) and a failure becomes the server's sentence. The
 * documents card and a step's form both upload through it.
 */
export function useCaseDocumentUpload(caseKind: CaseKind, caseId: string) {
  const api = DOCUMENTS_API[caseKind];
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = async (
    docType: DocumentType,
    file: File,
  ): Promise<CaseDocument | null> => {
    if (uploading) return null;
    const current =
      attempt && attempt.docType === docType && attempt.file === file
        ? attempt
        : { key: newIdempotencyKey(), docType, file };
    setAttempt(current);
    setUploading(true);
    setError(null);
    try {
      const created = await api.upload(caseId, {
        docType,
        file,
        idempotencyKey: current.key,
      });
      setAttempt(null);
      return created;
    } catch (caught) {
      setError(errorMessage(caught));
      return null;
    } finally {
      setUploading(false);
    }
  };

  return { upload, uploading, error, clearError: () => setError(null) };
}

/**
 * "Chứng từ" of one case: the documents attached to it, uploading the next
 * version of a type, and downloading one. The server decides who may and what
 * is accepted; the card draws what it said and locks upload with the reason
 * when the viewer cannot.
 */
export function CaseDocumentsCard({
  caseKind,
  caseId,
  canUpload,
  onUploaded,
  refreshTick = 0,
}: {
  caseKind: CaseKind;
  caseId: string;
  canUpload: boolean;
  /** Told after a document is stored, so a page can refresh what reads them. */
  onUploaded?: (document: CaseDocument) => void;
  /** Bumped by the page when a document was stored elsewhere (a step's form). */
  refreshTick?: number;
}) {
  const { message } = App.useApp();
  const online = useOnline();
  const api = DOCUMENTS_API[caseKind];
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [docType, setDocType] = useState<DocumentType | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const {
    upload: send,
    uploading,
    error: uploadError,
    clearError,
  } = useCaseDocumentUpload(caseKind, caseId);
  const [downloading, setDownloading] = useState<string | null>(null);

  const load = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      setList({ kind: "ready", documents: await api.list(caseId) });
    } catch (error) {
      setList({ kind: "error", error });
    }
  }, [api, caseId]);

  useEffect(() => {
    void load();
  }, [load, refreshTick]);

  const upload = async () => {
    if (!docType || !file) return;
    const created = await send(docType, file);
    if (!created) return;
    setFile(null);
    void message.success(
      `Đã tải lên ${DOC_TYPE_LABEL[created.doc_type]}, phiên bản ${created.version}.`,
    );
    onUploaded?.(created);
    await load();
  };

  const download = async (document_: CaseDocument) => {
    setDownloading(document_.id);
    try {
      saveBlob(
        await apiClient().downloadCaseDocument(document_.id),
        document_.filename,
      );
    } catch (error) {
      void message.error(errorMessage(error));
    } finally {
      setDownloading(null);
    }
  };

  const columns: TableColumnsType<CaseDocument> = [
    {
      title: "Loại chứng từ",
      dataIndex: "doc_type",
      render: (value: DocumentType) => DOC_TYPE_LABEL[value],
    },
    {
      title: "Phiên bản",
      dataIndex: "version",
      render: (value: number) => (
        <Typography.Text code>{`v${value}`}</Typography.Text>
      ),
    },
    {
      title: "Tên file",
      dataIndex: "filename",
      render: (value: string) => (
        <Typography.Text ellipsis={{ tooltip: value }} className="max-w-64">
          {value}
        </Typography.Text>
      ),
    },
    {
      title: "Kích thước",
      dataIndex: "size_bytes",
      align: "right",
      render: (value: number) => formatSize(value),
    },
    {
      title: `Tải lên lúc (${VN_TIME})`,
      dataIndex: "uploaded_at",
      render: (value: string) => formatDateTime(value),
    },
    {
      title: "",
      key: "download",
      fixed: "right",
      render: (_: unknown, row: CaseDocument) => (
        <Button
          icon={<DownloadOutlined aria-hidden />}
          loading={downloading === row.id}
          onClick={() => void download(row)}
        >
          Tải xuống
        </Button>
      ),
    },
  ];

  const lockReason = !canUpload
    ? NO_WRITE_REASON
    : !online
      ? OFFLINE_REASON
      : null;
  const withReason = (node: ReactNode) =>
    lockReason ? (
      <Tooltip title={lockReason}>
        <span>{node}</span>
      </Tooltip>
    ) : (
      node
    );

  const fileList: UploadFile[] = file
    ? [{ uid: "chosen", name: file.name, status: "done" }]
    : [];

  return (
    <Card
      title={
        <Flex align="center" gap="small">
          <PaperClipOutlined aria-hidden />
          Chứng từ
        </Flex>
      }
    >
      <Flex vertical gap="middle">
        {list.kind === "error" ? (
          <LoadError compact error={list.error} onRetry={() => void load()} />
        ) : (
          <Table<CaseDocument>
            rowKey="id"
            size="small"
            pagination={false}
            sticky
            scroll={{ x: "max-content" }}
            loading={list.kind === "loading"}
            columns={columns}
            dataSource={list.kind === "ready" ? list.documents : []}
            locale={{
              emptyText:
                list.kind === "loading" ? (
                  " "
                ) : (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="Hồ sơ chưa có chứng từ nào."
                  />
                ),
            }}
          />
        )}

        <Flex wrap gap="small" align="start">
          <Select<DocumentType>
            aria-label="Loại chứng từ"
            placeholder="Loại chứng từ"
            className="min-w-56"
            options={DOC_TYPE_OPTIONS}
            value={docType ?? undefined}
            onChange={(value) => setDocType(value)}
            disabled={lockReason !== null}
            virtual={false}
          />
          <Upload
            accept={ACCEPT}
            maxCount={1}
            fileList={fileList}
            disabled={lockReason !== null}
            beforeUpload={(chosen) => {
              setFile(chosen);
              clearError();
              return false;
            }}
            onRemove={() => {
              setFile(null);
              return true;
            }}
          >
            <Button
              icon={<PaperClipOutlined aria-hidden />}
              disabled={lockReason !== null}
            >
              Chọn file
            </Button>
          </Upload>
          {withReason(
            <Button
              type="primary"
              icon={<UploadOutlined aria-hidden />}
              loading={uploading}
              disabled={lockReason !== null || !docType || !file}
              onClick={() => void upload()}
            >
              Tải lên
            </Button>,
          )}
        </Flex>
        {lockReason && <Typography.Text>{lockReason}</Typography.Text>}
        {uploadError && <Alert type="error" showIcon title={uploadError} />}
        <Typography.Text type="secondary">
          Nhận PDF, JPEG, PNG, XLSX, DOCX, EML, MSG. Mỗi lần tải lên cùng loại
          là một phiên bản mới; phiên bản cũ vẫn giữ.
        </Typography.Text>
      </Flex>
    </Card>
  );
}
