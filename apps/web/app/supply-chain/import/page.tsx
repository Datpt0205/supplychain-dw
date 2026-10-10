"use client";

import { useMemo, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Segmented,
  Table,
  Tooltip,
  Typography,
  Upload,
} from "antd";
import type { TableColumnsType } from "antd";
import { DownloadOutlined, UploadOutlined } from "@ant-design/icons";
import { PageHeader, RegionState, StatusTag, type StatusTone } from "@dw/ui";
import type {
  ImportReport,
  ImportRow,
  ImportRowStatus,
  ImportSheet,
} from "@dw/api-client";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { saveBlob } from "../../../components/supply-chain/case-documents-card";
import {
  DRY_RUN_FIRST,
  OFFLINE_LOCK,
} from "../../../components/supply-chain/import-labels";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../lib/idempotency-key";
import { apiClient } from "../../../lib/session";

// The scope the three routes check; the server refuses without it, and this
// only decides what the screen offers.
const IMPORT_SCOPE = "supply_chain.import";

const SHEET_LABEL: Record<ImportSheet, string> = {
  suppliers: "NCC",
  catalogue: "Danh mục",
  users: "Người dùng",
  product_cases: "Hồ sơ SP",
  po_cases: "Hồ sơ PO",
};

/** Each status in words for a dry run and for a real run, and its tone. */
const STATUS_LABEL: Record<
  ImportRowStatus,
  { dry: string; applied: string; tone: StatusTone }
> = {
  created: { dry: "Sẽ thêm", applied: "Đã thêm", tone: "ok" },
  exists: { dry: "Đã có, bỏ qua", applied: "Đã có, bỏ qua", tone: "gray" },
  partial: { dry: "Thêm một phần", applied: "Thêm một phần", tone: "warn" },
  rejected: { dry: "Từ chối", applied: "Từ chối", tone: "err" },
};

type Filter = "all" | "rejected";

function ReportView({ report }: { report: ImportReport }) {
  const [filter, setFilter] = useState<Filter>("all");
  const rows = useMemo(
    () =>
      filter === "all"
        ? report.rows
        : report.rows.filter(
            (r) => r.status === "rejected" || r.status === "partial",
          ),
    [report.rows, filter],
  );
  const words = (status: ImportRowStatus) =>
    report.dry_run ? STATUS_LABEL[status].dry : STATUS_LABEL[status].applied;
  const columns: TableColumnsType<ImportRow> = [
    {
      title: "Sheet",
      dataIndex: "sheet",
      width: 110,
      render: (sheet: ImportSheet) => SHEET_LABEL[sheet],
    },
    { title: "Dòng", dataIndex: "row", width: 70, align: "right" },
    {
      title: "Khóa",
      dataIndex: "key",
      width: 200,
      render: (key: string) => (
        <Typography.Text code ellipsis={{ tooltip: key }}>
          {key || "—"}
        </Typography.Text>
      ),
    },
    {
      title: "Kết quả",
      dataIndex: "status",
      width: 150,
      render: (status: ImportRowStatus) => (
        <StatusTag tone={STATUS_LABEL[status].tone}>{words(status)}</StatusTag>
      ),
    },
    {
      title: "Ghi chú",
      dataIndex: "messages",
      render: (messages: string[]) => (
        <Flex vertical>
          {messages.map((m) => (
            <Typography.Text key={m}>{m}</Typography.Text>
          ))}
        </Flex>
      ),
    },
  ];
  const rejected = report.rows.filter((r) => r.status === "rejected").length;
  return (
    <Flex vertical gap="middle">
      <Alert
        type={report.dry_run ? "info" : rejected ? "warning" : "success"}
        showIcon
        message={
          report.dry_run
            ? "Chạy thử: chưa ghi gì vào hệ thống."
            : "Đã nạp. Dòng bị từ chối không làm hỏng dòng khác; sửa file rồi nạp lại, dòng đã có sẽ được bỏ qua."
        }
      />
      {report.problems.map((p) => (
        <Alert
          key={p.sheet}
          type="error"
          showIcon
          message={`Sheet ${SHEET_LABEL[p.sheet]} không đọc được`}
          description={p.message}
        />
      ))}
      <Table
        size="small"
        pagination={false}
        rowKey="sheet"
        dataSource={report.sheets}
        aria-label="Tổng theo sheet"
        columns={[
          { title: "Sheet", dataIndex: "title" },
          {
            title: report.dry_run ? "Sẽ thêm" : "Đã thêm",
            dataIndex: "created",
            align: "right",
          },
          { title: "Đã có", dataIndex: "exists", align: "right" },
          { title: "Một phần", dataIndex: "partial", align: "right" },
          { title: "Từ chối", dataIndex: "rejected", align: "right" },
        ]}
      />
      <Segmented<Filter>
        value={filter}
        onChange={setFilter}
        options={[
          { label: `Mọi dòng (${report.rows.length})`, value: "all" },
          { label: "Cần sửa", value: "rejected" },
        ]}
      />
      <Table
        size="small"
        rowKey={(r) => `${r.sheet}:${r.row}`}
        dataSource={rows}
        columns={columns}
        pagination={{ pageSize: 50, hideOnSinglePage: true }}
        scroll={{ x: 800 }}
        aria-label="Kết quả từng dòng"
      />
    </Flex>
  );
}

/**
 * Nạp một lần dữ liệu đang có (ADR 0027, ticket onboarding/01): NCC kèm liên
 * hệ và tài khoản, danh mục mã hàng và SKU, người dùng với vai và workspace,
 * từ mẫu Excel của hệ thống. Chạy thử trước (không ghi gì), xem từng dòng,
 * rồi mới nạp thật; nạp lại cùng file không tạo bản sao. Mọi quyết định ở
 * máy chủ: màn này chỉ không mời bấm khi thiếu quyền hay mất mạng.
 */
export default function SupplyChainImportPage() {
  const { message, modal } = App.useApp();
  const { hasScope } = useAuth();
  const online = useOnline();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  // The file the shown dry run was of: applying needs that same file.
  const [triedFile, setTriedFile] = useState<File | null>(null);
  const [applyKey, setApplyKey] = useState<string>(newIdempotencyKey);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  if (!hasScope(IMPORT_SCOPE)) {
    return (
      <div className="mx-auto max-w-6xl">
        <PageHeader
          breadcrumb={supplyChainCrumbs("Nạp dữ liệu")}
          title="Nạp dữ liệu có sẵn"
        />
        <Card>
          <RegionState
            kind="forbidden"
            description={`Nạp dữ liệu cần quyền ${IMPORT_SCOPE} (vai Quản trị công ty).`}
          />
        </Card>
      </div>
    );
  }

  const choose = (chosen: File) => {
    setFile(chosen);
    setReport(null);
    setTriedFile(null);
    setRefusal(null);
    setApplyKey(newIdempotencyKey());
    return false;
  };

  const run = async (mode: "dry-run" | "apply") => {
    if (!file) return;
    setBusy(true);
    setRefusal(null);
    try {
      const result = await apiClient().runImport(
        file,
        mode,
        mode === "apply" ? applyKey : newIdempotencyKey(),
      );
      setReport(result);
      if (mode === "dry-run") setTriedFile(file);
      else void message.success("Đã nạp; xem kết quả từng dòng bên dưới");
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const confirmApply = () =>
    modal.confirm({
      title: `Nạp thật file ${file?.name ?? ""}?`,
      content:
        "Hệ thống ghi từng dòng qua đúng quy trình (kiểm, quyền, nhật ký); NCC, danh mục và người dùng đã thêm không gỡ được từ màn này. Dòng đã có được bỏ qua.",
      okText: "Nạp thật",
      okButtonProps: { danger: true },
      autoFocusButton: "cancel",
      cancelText: "Hủy",
      onOk: () => run("apply"),
    });

  const download = async () => {
    try {
      saveBlob(
        await apiClient().downloadImportTemplate(),
        "mau-nap-du-lieu-supply-chain.xlsx",
      );
    } catch (error) {
      setRefusal(errorMessage(error));
    }
  };

  const canApply = Boolean(
    file && triedFile === file && report?.dry_run && online,
  );
  const lockReason = !file
    ? null
    : !online
      ? OFFLINE_LOCK
      : canApply
        ? null
        : DRY_RUN_FIRST;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Nạp dữ liệu")}
        title="Nạp dữ liệu có sẵn"
        subtitle="Một lần, từ mẫu Excel: NCC, danh mục mã hàng và SKU, người dùng, hồ sơ đang chạy (mở ở bước hiện tại, ngày khai báo). Không AI nào đọc file này; ô thiếu là dòng bị từ chối, không đoán."
      />
      <Flex vertical gap="middle">
        <Card title="1. Tải mẫu">
          <Flex gap="small" align="center" wrap>
            <Button icon={<DownloadOutlined />} onClick={() => void download()}>
              Tải mẫu Excel
            </Button>
            <Typography.Text type="secondary">
              Không đổi tên sheet hay tiêu đề cột; cột có dấu * là bắt buộc.
            </Typography.Text>
          </Flex>
        </Card>
        <Card title="2. Chạy thử, rồi nạp">
          <Flex vertical gap="small">
            <Upload
              accept=".xlsx"
              maxCount={1}
              beforeUpload={choose}
              onRemove={() => {
                setFile(null);
                setReport(null);
                setTriedFile(null);
              }}
              fileList={
                file ? [{ uid: "import", name: file.name, status: "done" }] : []
              }
            >
              <Button icon={<UploadOutlined />}>Chọn file .xlsx</Button>
            </Upload>
            <Flex gap="small" wrap>
              <Button
                type="primary"
                disabled={!file || !online}
                loading={busy}
                onClick={() => void run("dry-run")}
              >
                Chạy thử
              </Button>
              <Tooltip title={lockReason}>
                <Button
                  danger
                  disabled={!canApply}
                  loading={busy}
                  onClick={confirmApply}
                >
                  Nạp thật
                </Button>
              </Tooltip>
            </Flex>
            {lockReason ? (
              <Typography.Text>{lockReason}</Typography.Text>
            ) : null}
            {refusal ? <Alert type="error" showIcon message={refusal} /> : null}
          </Flex>
        </Card>
        {report ? (
          <Card title={report.dry_run ? "Kết quả chạy thử" : "Kết quả nạp"}>
            <ReportView report={report} />
          </Card>
        ) : null}
      </Flex>
    </div>
  );
}
