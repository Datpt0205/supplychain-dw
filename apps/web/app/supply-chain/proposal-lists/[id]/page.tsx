"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Input,
  Select,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { PageHeader, RegionState, StatusTag } from "@dw/ui";
import type { ProposalListDetail, ProposalRow } from "@dw/api-client";
import { LoadError } from "../../../../components/load-error";
import { supplyChainCrumbs } from "../../../../components/supply-chain/crumbs";
import { useProductCategories } from "../../../../components/supply-chain/product-categories";
import { errorMessage } from "../../../../lib/error-message";
import { useOnline } from "../../../../lib/hooks/use-online";
import { useAttemptKey } from "../../../../lib/idempotency-key";
import { apiClient } from "../../../../lib/session";
import { useCachedResource } from "../../../../lib/use-cached-resource";

export const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NEEDS_VALUES = "Cần mã đề xuất, tên sản phẩm và nhóm sản phẩm.";
export const PRIORITY_LABEL: Record<string, string> = {
  high: "Ưu tiên cao",
  normal: "Bình thường",
  low: "Ưu tiên thấp",
};
export const STATUS_TEXT: Record<
  NonNullable<ProposalListDetail["status"]>,
  string
> = {
  extracted: "AI đã đọc",
  unreadable: "Máy không đọc được file này; hãy đề xuất tay.",
  refused: "AI không đọc được danh sách theo mẫu; hãy đề xuất tay.",
  failed: "Đọc file bị lỗi; hãy tải lại hoặc đề xuất tay.",
};

type Values = { proposal_code: string; product_name: string; category: string };

function initial(row: ProposalRow): Values {
  return {
    proposal_code: row.fields.proposal_code?.value ?? "",
    product_name: row.fields.product_name?.value ?? "",
    // AI's Category stays a suggestion beside the field, never its value.
    category: "",
  };
}

function Read({ row, name }: { row: ProposalRow; name: string }) {
  const cited = row.fields[name];
  if (cited)
    return (
      <Tooltip title={`Máy đọc: “${cited.quote}”`}>
        <Typography.Text>{cited.value}</Typography.Text>
      </Tooltip>
    );
  return row.gaps.includes(name) ? (
    <StatusTag tone="unk">Thiếu</StatusTag>
  ) : null;
}

/**
 * Một danh sách SP đề xuất như AI đã đọc: mỗi dòng có giá trị máy đọc kèm trích
 * dẫn, ô thiếu, phát hiện trùng mã, gợi ý nhóm và mức ưu tiên kèm lý do. PIC
 * sửa ô rồi bấm "Đề xuất" cho từng dòng (thành hồ sơ, PIC là người bấm) hoặc
 * "Bỏ dòng". Không có nút làm cả danh sách một lần.
 */
export default function ProposalListPage() {
  const { id } = useParams<{ id: string }>();
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const categories = useProductCategories();
  const resource = useCachedResource(
    `supply-chain:proposal-list:${id}`,
    useCallback(() => apiClient().getProposalList(id), [id]),
  );
  const [values, setValues] = useState<Record<number, Values>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const detail = resource.data;

  if (!detail) {
    return (
      <div>
        <PageHeader
          breadcrumb={supplyChainCrumbs(
            {
              title: "Danh sách đề xuất",
              href: "/supply-chain/proposal-lists",
            },
            "Danh sách",
          )}
          title="Danh sách SP đề xuất"
        />
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} />
        ) : (
          <RegionState kind="loading" />
        )}
      </div>
    );
  }

  const valuesOf = (row: ProposalRow): Values =>
    values[row.index] ?? initial(row);
  const setValue = (row: ProposalRow, name: keyof Values, value: string) =>
    setValues((old) => ({
      ...old,
      [row.index]: { ...valuesOf(row), [name]: value },
    }));

  const propose = async (row: ProposalRow) => {
    const body = valuesOf(row);
    setBusy(row.index);
    setRefusal(null);
    try {
      await apiClient().proposeFromList(
        id,
        row.index,
        {
          proposal_code: body.proposal_code.trim(),
          product_name: body.product_name.trim(),
          category: body.category,
        },
        attemptKey({ index: row.index, ...body }),
      );
      void message.success(`Đã đề xuất ${body.proposal_code}`);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const drop = async (row: ProposalRow) => {
    setBusy(row.index);
    setRefusal(null);
    try {
      await apiClient().dropFromList(
        id,
        row.index,
        null,
        attemptKey({ index: row.index, drop: true }),
      );
      void message.success("Đã bỏ dòng");
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setBusy(null);
    }
  };

  const options = (categories.data ?? []).map((c) => ({
    value: c.key,
    label: c.label,
  }));

  const columns: TableColumnsType<ProposalRow> = [
    { title: "#", key: "index", render: (_, row) => row.index + 1 },
    {
      title: "Máy đọc",
      key: "read",
      render: (_, row) => (
        <Flex vertical gap={2}>
          <Read row={row} name="product_name" />
          <Read row={row} name="proposal_code" />
          <Read row={row} name="supplier_name" />
          <Read row={row} name="item_code" />
          <Read row={row} name="image_ref" />
        </Flex>
      ),
    },
    {
      title: "Mã đề xuất",
      key: "proposal_code",
      render: (_, row) => (
        <Input
          aria-label={`Mã đề xuất dòng ${row.index + 1}`}
          value={valuesOf(row).proposal_code}
          disabled={row.decision !== null}
          onChange={(e) => setValue(row, "proposal_code", e.target.value)}
        />
      ),
    },
    {
      title: "Tên sản phẩm",
      key: "product_name",
      render: (_, row) => (
        <Input
          aria-label={`Tên sản phẩm dòng ${row.index + 1}`}
          value={valuesOf(row).product_name}
          disabled={row.decision !== null}
          onChange={(e) => setValue(row, "product_name", e.target.value)}
        />
      ),
    },
    {
      title: "Nhóm sản phẩm",
      key: "category",
      render: (_, row) => (
        <Flex vertical gap={4}>
          <Select
            aria-label={`Nhóm sản phẩm dòng ${row.index + 1}`}
            className="min-w-40"
            options={options}
            value={valuesOf(row).category || undefined}
            placeholder="Chọn nhóm"
            disabled={row.decision !== null}
            onChange={(value: string) => setValue(row, "category", value)}
          />
          {row.category && row.decision === null && (
            <Flex gap={4} align="center" wrap>
              <StatusTag tone="geek">AI gợi ý</StatusTag>
              <Button
                size="small"
                onClick={() => setValue(row, "category", row.category ?? "")}
              >
                {options.find((o) => o.value === row.category)?.label ??
                  row.category}
              </Button>
              {row.category_reason && (
                <Typography.Text>{row.category_reason}</Typography.Text>
              )}
            </Flex>
          )}
        </Flex>
      ),
    },
    {
      title: "Phát hiện",
      key: "findings",
      render: (_, row) => (
        <Flex vertical gap={2}>
          {row.priority && (
            <Typography.Text>
              {`AI gợi ý: ${PRIORITY_LABEL[row.priority] ?? row.priority}`}
              {row.priority_reason ? ` (${row.priority_reason})` : ""}
            </Typography.Text>
          )}
          {row.findings.map((f) => (
            <StatusTag key={f.code} tone="warn">
              {f.message}
            </StatusTag>
          ))}
        </Flex>
      ),
    },
    {
      title: "Xử lý",
      key: "actions",
      fixed: "right",
      render: (_, row) => {
        if (row.decision?.decision === "proposed")
          return row.decision.product_dev_case_id ? (
            <Link
              href={`/supply-chain/product-cases/${row.decision.product_dev_case_id}`}
            >
              Đã đề xuất: mở hồ sơ
            </Link>
          ) : (
            <StatusTag tone="ok">Đã đề xuất</StatusTag>
          );
        if (row.decision?.decision === "dropped")
          return <StatusTag tone="gray">Đã bỏ</StatusTag>;
        const v = valuesOf(row);
        const lock = !online
          ? OFFLINE
          : !v.proposal_code.trim() || !v.product_name.trim() || !v.category
            ? NEEDS_VALUES
            : null;
        return (
          <Flex gap="small" wrap>
            <Tooltip title={lock ?? undefined}>
              <Button
                type="primary"
                disabled={lock !== null}
                loading={busy === row.index}
                onClick={() => void propose(row)}
              >
                Đề xuất
              </Button>
            </Tooltip>
            <Button
              disabled={!online || busy !== null}
              onClick={() => void drop(row)}
            >
              Bỏ dòng
            </Button>
          </Flex>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader
        breadcrumb={supplyChainCrumbs(
          { title: "Danh sách đề xuất", href: "/supply-chain/proposal-lists" },
          detail.filename,
        )}
        title={detail.filename}
        subtitle="Kiểm từng dòng AI đọc, sửa ô, chọn nhóm, rồi đề xuất hoặc bỏ dòng. Mã trùng vẫn bị hệ thống từ chối khi đề xuất."
      />
      <Card>
        <Flex vertical gap="middle">
          {refusal && <Alert type="error" showIcon title={refusal} />}
          {detail.status === null ? (
            <Alert
              type="info"
              showIcon
              title="AI đang đọc danh sách; bạn sẽ được báo khi xong."
            />
          ) : detail.status !== "extracted" ? (
            <Alert type="warning" showIcon title={STATUS_TEXT[detail.status]} />
          ) : null}
          <Table<ProposalRow>
            rowKey="index"
            size="small"
            columns={columns}
            dataSource={detail.rows}
            pagination={false}
            scroll={{ x: "max-content" }}
            locale={{
              emptyText: (
                <Empty description="Chưa có dòng nào AI đọc được từ file này." />
              ),
            }}
          />
          <Typography.Text>
            {`${detail.rows.filter((r) => r.decision === null).length} dòng chờ xử lý trên ${detail.rows.length}.`}
          </Typography.Text>
        </Flex>
      </Card>
    </div>
  );
}
