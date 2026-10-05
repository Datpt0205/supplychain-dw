"use client";

import { Suspense, useCallback } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PackageSearch, X } from "lucide-react";
import { caseStateSchema } from "@dw/contracts";
import {
  Badge,
  Button,
  Label,
  Select,
  Skeleton,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@dw/ui";
import { EmptyState } from "../../../components/empty-state";
import { LoadMore } from "../../../components/load-more";
import { PageHeading } from "../../../components/page-heading";
import {
  CASE_STATE_LABEL,
  CaseStateBadge,
} from "../../../components/supply-chain/case-state-badge";
import { formatDateTime } from "../../../lib/dates";
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
  // component it would not help. A green `next build` does not prove this
  // placement: AppFrame renders a loader until auth resolves, so the body
  // is never prerendered today either way.
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <POCasesView />
    </Suspense>
  );
}

const STATE_SELECT_ID = "po-cases-state";

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
   * round trip, where `router.replace` fetched the page segment again and
   * the select snapped back to its old value until that returned. */
  const apply = (next: ListFilter) => {
    window.history.replaceState(null, "", poCasesHref(next));
  };
  /** Focus back on the state picker when the control that had it is about
   * to unmount (the chip's X, the empty state's clear button) — otherwise
   * keyboard focus falls to <body>. */
  const refocus = () => document.getElementById(STATE_SELECT_ID)?.focus();

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <PageHeading
        icon={PackageSearch}
        title="PO cases"
        description="Purchase order theo trạng thái, SLA và cập nhật từ nhà cung cấp."
      />
      <div className="flex flex-wrap items-center gap-4">
        <Select
          id={STATE_SELECT_ID}
          aria-label="Lọc theo trạng thái"
          className="w-60"
          value={filter.state ?? ""}
          onChange={(event) =>
            apply({
              ...filter,
              state: caseStateSchema.safeParse(event.target.value).data,
            })
          }
        >
          <option value="">Tất cả trạng thái</option>
          {caseStateSchema.options.map((option) => (
            <option key={option} value={option}>
              {CASE_STATE_LABEL[option]}
            </option>
          ))}
        </Select>
        <div className="flex items-center gap-2">
          <Switch
            id="po-cases-active-only"
            checked={filter.activeOnly}
            onChange={(event) =>
              apply({ ...filter, activeOnly: event.target.checked })
            }
          />
          <Label htmlFor="po-cases-active-only">Chỉ case đang chạy</Label>
        </div>
        {filter.supplierName !== undefined && (
          <Badge variant="secondary" className="gap-1.5">
            Nhà cung cấp: {filter.supplierName}
            <button
              type="button"
              aria-label={`Bỏ lọc nhà cung cấp ${filter.supplierName}`}
              className="rounded-sm hover:text-foreground"
              onClick={() => {
                apply({ ...filter, supplierName: undefined });
                refocus();
              }}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        )}
      </div>
      {/* Keyed on the filter: a filter change is a fresh list, never the
          new first page spliced onto the old filter's page two, which the
          shared hooks would otherwise show for a frame (their state
          outlives a key change until the next effect runs). */}
      <POCaseResults
        key={JSON.stringify([
          filter.state,
          filter.supplierName,
          filter.activeOnly,
        ])}
        filter={filter}
        filtered={filtered}
        onClearFilters={() => {
          apply({ activeOnly: false });
          refocus();
        }}
      />
    </div>
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
  const { items, loading, error, hasMore, loadingMore, loadMore } =
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

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (error != null && items.length === 0) {
    // A failed load is never shown as "no cases": that would contradict
    // the Control Tower row that linked here.
    return (
      <p className="text-sm text-destructive">
        Không tải được danh sách PO case:{" "}
        {error instanceof Error ? error.message : "lỗi không xác định"}
      </p>
    );
  }
  if (items.length === 0) {
    return filtered ? (
      <EmptyState
        icon={PackageSearch}
        title="Không có case nào khớp bộ lọc"
        description="Bỏ bớt điều kiện lọc để xem thêm case."
        action={
          <Button variant="outline" size="sm" onClick={onClearFilters}>
            Xoá bộ lọc
          </Button>
        }
      />
    ) : (
      <EmptyState
        icon={PackageSearch}
        title="Chưa có PO case nào"
        description="PO case được tạo qua API sẽ hiện ở đây."
      />
    );
  }
  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>PO reference</TableHead>
            <TableHead>Nhà cung cấp</TableHead>
            <TableHead>Trạng thái</TableHead>
            <TableHead>Tạo lúc</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.id}>
              <TableCell className="font-medium">
                <Link
                  href={`/supply-chain/po-cases/${item.id}`}
                  className="hover:underline"
                >
                  {item.po_reference}
                </Link>
              </TableCell>
              <TableCell>{item.supplier_name}</TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1.5">
                  <CaseStateBadge state={item.state} />
                  {item.interrupted_state && (
                    <Badge variant="outline">
                      tạm dừng tại {item.interrupted_state}
                    </Badge>
                  )}
                </div>
              </TableCell>
              <TableCell className="whitespace-nowrap text-sm text-muted-foreground">
                {formatDateTime(item.created_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <LoadMore
        hasMore={hasMore}
        loading={loadingMore}
        onLoadMore={loadMore}
        shown={items.length}
        noun="case"
      />
    </>
  );
}
