"use client";

import { Suspense, useCallback, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Alert,
  Button,
  Empty,
  Flex,
  Input,
  Segmented,
  Select,
  Skeleton,
  Table,
  Tag,
  Typography,
  type TableColumnsType,
} from "antd";
import { CloseOutlined } from "@ant-design/icons";
import { caseStateSchema, type CaseState, type POCase } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import {
  CASE_STATE_LABEL,
  CASE_STATE_META,
  CaseStateTag,
  stepLabel,
} from "../../../components/supply-chain/case-state-badge";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { formatDateTime, VN_TIME } from "../../../lib/dates";
import { errorMessage, regionFailure } from "../../../lib/error-message";
import { matches } from "../../../lib/search";
import { apiClient } from "../../../lib/session";
import {
  poCasesHref,
  readPOCaseFilter,
  type ListFilter,
} from "../../../lib/supply-chain/po-case-filter";
import { useCachedPages } from "../../../lib/use-cached-pages";

/** The Case Workspace's own entry point: every PO case for this workspace,
 * newest first, narrowed by whatever the URL says — which is how the
 * Control Tower's rows drill down here, and why a filtered view can be
 * bookmarked or shared. `apps/supply-chain/po-cases/[id]` is the Case
 * Workspace itself. */
export default function POCasesPage() {
  // `useSearchParams` has no value during a static prerender, so the
  // boundary sits ABOVE the component that calls it — in the same
  // component it would not help.
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 6 }} />}>
      <POCasesView />
    </Suspense>
  );
}

const STATE_SELECT_ID = "po-cases-state";

const STATE_OPTIONS = caseStateSchema.options.map((value) => ({
  value,
  label: CASE_STATE_LABEL[value],
}));

function POCasesView() {
  const searchParams = useSearchParams();
  const filter = readPOCaseFilter(searchParams);
  const filtered =
    filter.state !== undefined ||
    filter.supplierName !== undefined ||
    filter.activeOnly;

  /** Writes the filter back to the URL — its only home, so back/forward
   * and a shared link land on the same view. `history.replaceState`, not
   * `router.replace`: Next syncs `useSearchParams` from it without a server
   * round trip. */
  const apply = (next: ListFilter) => {
    window.history.replaceState(null, "", poCasesHref(next));
  };
  /** Focus back on the state picker when the control that had it is about
   * to unmount (a chip's remove, the clear buttons) — otherwise keyboard
   * focus falls to <body>. */
  const refocus = () => document.getElementById(STATE_SELECT_ID)?.focus();
  const clearAll = () => {
    apply({ activeOnly: false });
    refocus();
  };

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Hồ sơ PO")}
        title="Hồ sơ PO"
        description="Đơn đặt hàng với NCC, từ Tạo PO (bước 10) tới Nhập kho (bước 17). Mới nhất trước."
      />
      <Flex vertical gap="middle">
        <Flex wrap gap="middle" align="center">
          <Segmented<"active" | "all">
            aria-label="Phạm vi hồ sơ"
            value={filter.activeOnly ? "active" : "all"}
            options={[
              { value: "all", label: "Tất cả" },
              { value: "active", label: "Đang chạy" },
            ]}
            onChange={(value) =>
              apply({ ...filter, activeOnly: value === "active" })
            }
          />
          <Select<CaseState | "">
            id={STATE_SELECT_ID}
            aria-label="Lọc theo trạng thái"
            className="min-w-60"
            value={filter.state ?? ""}
            options={[{ value: "", label: "Mọi trạng thái" }, ...STATE_OPTIONS]}
            onChange={(value) =>
              apply({ ...filter, state: value === "" ? undefined : value })
            }
            virtual={false}
          />
        </Flex>
        {filtered && (
          <Flex wrap gap="small" align="center">
            <Typography.Text>Đang lọc:</Typography.Text>
            {filter.activeOnly && (
              <FilterChip
                label="Đang chạy"
                onRemove={() => {
                  apply({ ...filter, activeOnly: false });
                  refocus();
                }}
              />
            )}
            {filter.state && (
              <FilterChip
                label={`Trạng thái: ${CASE_STATE_LABEL[filter.state]}`}
                onRemove={() => {
                  apply({ ...filter, state: undefined });
                  refocus();
                }}
              />
            )}
            {filter.supplierName !== undefined && (
              <FilterChip
                label={`NCC: ${filter.supplierName}`}
                removeLabel={`Bỏ lọc nhà cung cấp ${filter.supplierName}`}
                onRemove={() => {
                  apply({ ...filter, supplierName: undefined });
                  refocus();
                }}
              />
            )}
            <Button type="link" size="small" onClick={clearAll}>
              Xóa tất cả
            </Button>
          </Flex>
        )}
        {/* Keyed on the filter: a filter change is a fresh list, never the
            new first page spliced onto the old filter's page two. */}
        <POCaseResults
          key={JSON.stringify([
            filter.state,
            filter.supplierName,
            filter.activeOnly,
          ])}
          filter={filter}
          filtered={filtered}
          onClearFilters={clearAll}
        />
      </Flex>
    </div>
  );
}

/** An active filter as a chip, removed by its own button. */
function FilterChip({
  label,
  removeLabel,
  onRemove,
}: {
  label: string;
  removeLabel?: string;
  onRemove: () => void;
}) {
  return (
    <Tag className="me-0">
      {label}
      <Button
        type="text"
        size="small"
        icon={<CloseOutlined aria-hidden />}
        aria-label={removeLabel ?? `Bỏ lọc ${label}`}
        onClick={onRemove}
      />
    </Tag>
  );
}

function POCaseResults({
  filter,
  filtered,
  onClearFilters,
}: {
  filter: ListFilter;
  filtered: boolean;
  onClearFilters: () => void;
}) {
  const { state, supplierName, activeOnly } = filter;
  const [query, setQuery] = useState("");
  const { items, loading, error, hasMore, loadingMore, loadMore, reload } =
    useCachedPages(
      // Every input the fetch depends on is in the key, so one filter's
      // cached first page is never served for another.
      `supply-chain:po-cases:${JSON.stringify([state, supplierName, activeOnly])}`,
      useCallback(
        (cursor: string | null) =>
          apiClient().listPOCases({ cursor, state, supplierName, activeOnly }),
        [state, supplierName, activeOnly],
      ),
    );

  // A failed load is never shown as "no cases": that would contradict the
  // Control Tower row that linked here.
  if (error != null && items.length === 0) {
    return (
      <RegionState
        failure={regionFailure(error)}
        what="danh sách Hồ sơ PO"
        onRetry={reload}
      />
    );
  }

  const shown = items.filter((item) =>
    matches(query, [item.po_reference, item.supplier_name]),
  );

  const columns: TableColumnsType<POCase> = [
    {
      title: "Hồ sơ PO",
      key: "case",
      render: (_: unknown, row) => (
        <Flex vertical>
          <Link href={`/supply-chain/po-cases/${row.id}`}>
            <Typography.Text code>{row.po_reference}</Typography.Text>
          </Link>
          <Typography.Text type="secondary">
            {row.supplier_name}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: "Trạng thái",
      key: "state",
      render: (_: unknown, row) => (
        <Flex vertical gap={2} align="start">
          <CaseStateTag state={row.state} />
          <Typography.Text type="secondary">
            {row.interrupted_state
              ? `Tạm dừng tại ${CASE_STATE_LABEL[row.interrupted_state]}`
              : stepLabel(CASE_STATE_META[row.state].step)}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: `Tạo lúc (${VN_TIME})`,
      dataIndex: "created_at",
      responsive: ["md"],
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <Flex vertical gap="small">
      <Input.Search
        allowClear
        aria-label="Tìm theo số PO hoặc NCC"
        placeholder="Ví dụ: PO-2026-007 hoặc tên NCC"
        className="max-w-md"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <Table<POCase>
        rowKey="id"
        size="middle"
        pagination={false}
        sticky
        scroll={{ x: "max-content" }}
        loading={loading}
        columns={columns}
        dataSource={shown}
        locale={{
          emptyText: loading ? (
            " "
          ) : items.length > 0 ? (
            <Empty
              description={`Không hồ sơ nào trong ${items.length} hồ sơ đã tải khớp "${query}".`}
            >
              <Button onClick={() => setQuery("")}>Xóa ô tìm</Button>
            </Empty>
          ) : filtered ? (
            <Empty description="Không có Hồ sơ PO nào khớp bộ lọc.">
              <Button onClick={onClearFilters}>Xóa bộ lọc</Button>
            </Empty>
          ) : (
            <Empty description="Chưa có Hồ sơ PO nào. Hồ sơ PO mở ở bước 10 (Tạo PO)." />
          ),
        }}
      />
      {!loading && items.length > 0 && (
        <Flex justify="space-between" align="center" wrap gap="small">
          <Typography.Text role="status">
            {hasMore
              ? `Đã tải ${items.length} hồ sơ; còn nữa. Ô tìm chỉ tìm trong hồ sơ đã tải.`
              : `Đã tải tất cả ${items.length} hồ sơ.`}
          </Typography.Text>
          {hasMore && (
            <Button loading={loadingMore} onClick={loadMore}>
              Tải thêm
            </Button>
          )}
        </Flex>
      )}
      {error != null && items.length > 0 && (
        <Alert type="error" showIcon title={errorMessage(error)} />
      )}
    </Flex>
  );
}
