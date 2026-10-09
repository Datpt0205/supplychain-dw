"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Table,
  Typography,
  Upload,
} from "antd";
import type { TableColumnsType } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { PageHeader, RegionState } from "@dw/ui";
import type { ProposalListSummary } from "@dw/api-client";
import { LoadError } from "../../../components/load-error";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { formatDateTimeFull, VN_TIME } from "../../../lib/dates";
import { errorMessage } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../lib/idempotency-key";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

export const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
const ACCEPT = ".pdf,.docx,.xlsx,.eml,.png,.jpg,.jpeg";

/**
 * Bước 1 từ một danh sách (ticket ai-automation/08): PIC tải lên danh sách SP
 * đề xuất (PDF có chữ, DOCX, XLSX, EML; ảnh thì máy không đọc được), AI đọc
 * thành từng dòng ở nền, rồi PIC mở danh sách để đề xuất hay bỏ từng dòng.
 * Không dòng nào thành hồ sơ khi chưa có người bấm.
 */
export default function ProposalListsPage() {
  const { message } = App.useApp();
  const router = useRouter();
  const online = useOnline();
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const resource = useCachedResource(
    "supply-chain:proposal-lists",
    useCallback(() => apiClient().listProposalLists(), []),
  );

  const upload = async (file: File) => {
    setBusy(true);
    setRefusal(null);
    try {
      const stored = await apiClient().uploadProposalList(
        file,
        newIdempotencyKey(),
      );
      void message.success(
        "Đã tải lên; AI đang đọc danh sách, bạn sẽ được báo khi xong",
      );
      router.push(`/supply-chain/proposal-lists/${stored.id}`);
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const columns: TableColumnsType<ProposalListSummary> = [
    {
      title: "File",
      key: "filename",
      render: (_, list) => (
        <Link href={`/supply-chain/proposal-lists/${list.id}`}>
          {list.filename}
        </Link>
      ),
    },
    {
      title: `Tải lên (${VN_TIME})`,
      key: "created_at",
      render: (_, list) => formatDateTimeFull(list.created_at),
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumb={supplyChainCrumbs("Danh sách đề xuất")}
        title="Danh sách SP đề xuất"
        subtitle="Tải danh sách lên, AI tách thành từng dòng, kiểm trùng mã và gợi ý nhóm; bạn đề xuất từng dòng."
      />
      <Flex vertical gap="middle">
        <Card title="Tải danh sách lên">
          <Flex vertical gap="small">
            {refusal && <Alert type="error" showIcon title={refusal} />}
            <Upload
              accept={ACCEPT}
              maxCount={1}
              showUploadList={false}
              disabled={!online || busy}
              beforeUpload={(file) => {
                void upload(file);
                return false;
              }}
            >
              <Button
                type="primary"
                icon={<UploadOutlined />}
                loading={busy}
                disabled={!online}
              >
                Chọn file danh sách
              </Button>
            </Upload>
            {!online && <Typography.Text>{OFFLINE}</Typography.Text>}
            <Typography.Text>
              PDF có chữ, DOCX, XLSX hoặc EML, tối đa 10 MB. Ảnh hay bản quét
              thì máy không đọc được; hãy đề xuất tay.
            </Typography.Text>
          </Flex>
        </Card>
        <Card title="Danh sách đã tải">
          {resource.data ? (
            <Table<ProposalListSummary>
              rowKey="id"
              size="small"
              columns={columns}
              dataSource={resource.data}
              pagination={false}
              scroll={{ x: "max-content" }}
              locale={{
                emptyText: (
                  <Empty description="Chưa có danh sách nào. Tải một file lên để AI tách thành từng dòng." />
                ),
              }}
            />
          ) : resource.error != null ? (
            <LoadError
              error={resource.error}
              onRetry={resource.reload}
              compact
            />
          ) : (
            <RegionState kind="loading" compact />
          )}
        </Card>
      </Flex>
    </div>
  );
}
