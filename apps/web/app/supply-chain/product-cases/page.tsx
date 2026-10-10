"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Alert,
  App,
  Button,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { CloseOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { PageHeader } from "@dw/ui";
import { LoadError } from "../../../components/load-error";
import {
  productDevStateSchema,
  type ProductCase,
  type ProductDevState,
} from "@dw/contracts";
import {
  productCasesHref,
  readProductCaseFilter,
  type ProductListFilter,
} from "../../../lib/supply-chain/product-case-filter";
import { stepLabel } from "../../../components/supply-chain/case-state-badge";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import {
  categoryLabel,
  useProductCategories,
} from "../../../components/supply-chain/product-categories";
import {
  PRODUCT_DEV_STATE_LABEL,
  PRODUCT_DEV_STATE_META,
  ProductDevStateTag,
  dutyLock,
  dutyScope,
} from "../../../components/supply-chain/product-case-labels";
import { useAuth } from "../../../lib/auth/auth-context";
import { formatDateTime, VN_TIME } from "../../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../../lib/directory";
import { errorMessage } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../lib/idempotency-key";
import { matches } from "../../../lib/search";
import { apiClient } from "../../../lib/session";

/**
 * Hồ sơ phát triển sản phẩm: every product proposed in this workspace, newest
 * first, narrowed by state or by "mine" (the PIC filter only narrows: everyone
 * with the read sees every case, QE-18), and, arriving from a command-bar or
 * Zalo answer (ticket 08), by a named PIC or a Category. The filter lives in
 * the URL, so a filtered view can be sent to a colleague. Proposing opens a short form; the
 * product's images are uploaded on the case page once it exists.
 */
export default function ProductCasesPage() {
  // `useSearchParams` needs a Suspense boundary above the component calling it.
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 6 }} />}>
      <ProductCasesView />
    </Suspense>
  );
}

const STATE_OPTIONS = productDevStateSchema.options.map((value) => ({
  value,
  label: PRODUCT_DEV_STATE_LABEL[value],
}));

type ListState =
  | { kind: "loading" }
  | { kind: "error"; error: unknown }
  | {
      kind: "ready";
      items: ProductCase[];
      nextCursor: string | null;
      loadingMore: boolean;
      moreError: string | null;
    };

function ProductCasesView() {
  const searchParams = useSearchParams();
  const filter = readProductCaseFilter(searchParams);
  const { principalId } = useAuth();
  const members = useWorkspaceMembers();
  const categories = useProductCategories().data;
  const [proposing, setProposing] = useState(false);

  // The URL is the filter's only home, so back/forward and a shared link land
  // on the same view; `replaceState` keeps Next's params in sync without a
  // server round trip.
  const apply = (next: ProductListFilter) => {
    window.history.replaceState(null, "", productCasesHref(next));
  };
  const filtered =
    filter.state !== undefined ||
    filter.mine ||
    filter.pic !== undefined ||
    filter.category !== undefined;
  // A named PIC narrows as "mine" does; with both, the named one.
  const picUserId =
    filter.pic ?? (filter.mine ? (principalId ?? undefined) : undefined);

  const refocus = () => document.getElementById(STATE_SELECT_ID)?.focus();
  const clearAll = () => {
    apply({ mine: false });
    refocus();
  };

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Phát triển SP")}
        title="Hồ sơ phát triển sản phẩm"
        subtitle="Sản phẩm Cung ứng đề xuất ở bước 1, qua lấy mẫu, R&D test mẫu và BGĐ duyệt (bước 6) tới BM04. Mới nhất trước."
        actions={<ProposeButton onOpen={() => setProposing(true)} />}
      />
      <Flex vertical gap="middle">
        <Flex wrap gap="middle" align="center">
          <Segmented<"all" | "mine">
            aria-label="Hồ sơ của ai"
            value={filter.mine ? "mine" : "all"}
            disabled={principalId === null}
            options={[
              { value: "all", label: "Tất cả hồ sơ" },
              { value: "mine", label: "Tôi là PIC" },
            ]}
            onChange={(value) => apply({ ...filter, mine: value === "mine" })}
          />
          <Select<ProductDevState | "">
            id={STATE_SELECT_ID}
            aria-label="Lọc theo trạng thái"
            className="min-w-56"
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
            {filter.mine && (
              <FilterChip
                label="Tôi là PIC"
                onRemove={() => {
                  apply({ ...filter, mine: false });
                  refocus();
                }}
              />
            )}
            {filter.pic && (
              <FilterChip
                label={`PIC: ${memberName(members, filter.pic)}`}
                onRemove={() => {
                  apply({ ...filter, pic: undefined });
                  refocus();
                }}
              />
            )}
            {filter.category && (
              <FilterChip
                label={`Category: ${categoryLabel(categories, filter.category)}`}
                onRemove={() => {
                  apply({ ...filter, category: undefined });
                  refocus();
                }}
              />
            )}
            {filter.state && (
              <FilterChip
                label={`Trạng thái: ${PRODUCT_DEV_STATE_LABEL[filter.state]}`}
                onRemove={() => {
                  apply({ ...filter, state: undefined });
                  refocus();
                }}
              />
            )}
            <Button type="link" size="small" onClick={clearAll}>
              Xóa tất cả
            </Button>
          </Flex>
        )}

        {/* Keyed on the filter: a new filter is a fresh list, never page two
            of the old one spliced on. */}
        <ProductCaseResults
          key={JSON.stringify([filter.state, picUserId, filter.category])}
          state={filter.state}
          picUserId={picUserId}
          category={filter.category}
          filtered={filtered}
          onClearFilters={clearAll}
          onPropose={() => setProposing(true)}
        />
      </Flex>
      <ProposeModal open={proposing} onClose={() => setProposing(false)} />
    </div>
  );
}

const STATE_SELECT_ID = "product-cases-state";

/** An active filter as a chip, removed by its own button. */
function FilterChip({
  label,
  onRemove,
}: {
  label: string;
  onRemove: () => void;
}) {
  return (
    <Tag className="me-0">
      {label}
      <Button
        type="text"
        size="small"
        icon={<CloseOutlined aria-hidden />}
        aria-label={`Bỏ lọc ${label}`}
        onClick={onRemove}
      />
    </Tag>
  );
}

// Opening a product case (lead decision 9). Owner: `PRODUCT_CASE_WRITE` in
// dw_supply_chain/application/handlers.py; a stale copy here fails closed.
const PRODUCT_CASE_WRITE = "supply_chain.product_case.write";

/** The propose button, locked with its reason when the viewer's roles lack the
 * write that opens a case or the duty the tenant's policy gives `propose`.
 * The server checks both again. */
function ProposeButton({ onOpen }: { onOpen: () => void }) {
  const { hasScope } = useAuth();
  const online = useOnline();
  const [requiredScope, setRequiredScope] = useState<string | null>(null);
  const [dutiesError, setDutiesError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiClient()
      .getProductActionDuties()
      .then((duties) => {
        const duty = duties.action_duties.propose;
        if (!cancelled && duty) setRequiredScope(dutyScope(duty));
      })
      .catch((error: unknown) => {
        if (!cancelled) setDutiesError(errorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reason = dutiesError
    ? `Chưa đọc được ai được đề xuất: ${dutiesError}`
    : requiredScope === null
      ? "Đang kiểm tra quyền đề xuất…"
      : !hasScope(PRODUCT_CASE_WRITE)
        ? "Đề xuất cần quyền mở hồ sơ phát triển sản phẩm; vai của bạn chưa có."
        : !hasScope(requiredScope)
          ? dutyLock(requiredScope)
          : !online
            ? "Không có kết nối mạng. Kết nối lại rồi thử lại."
            : null;
  const button = (
    <Button
      type="primary"
      icon={<PlusOutlined aria-hidden />}
      disabled={reason !== null}
      onClick={onOpen}
    >
      Đề xuất sản phẩm
    </Button>
  );
  return (
    <Flex vertical align="end" gap="small">
      {reason ? (
        <Tooltip title={reason}>
          <span>{button}</span>
        </Tooltip>
      ) : (
        button
      )}
      {reason && requiredScope !== null && (
        <Typography.Text>{reason}</Typography.Text>
      )}
    </Flex>
  );
}

interface ProposeValues {
  proposalCode: string;
  productName: string;
  category: string;
}

/** Step 1. JSON only; images go up on the case page after it exists. One
 * idempotency key per press, reused when the same values are retried. */
function ProposeModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [form] = Form.useForm<ProposeValues>();
  const { message } = App.useApp();
  const router = useRouter();
  const categories = useProductCategories();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invalid, setInvalid] = useState(0);
  const [attempt, setAttempt] = useState<{
    key: string;
    values: string;
  } | null>(null);

  const submit = async (values: ProposeValues) => {
    setInvalid(0);
    const fingerprint = JSON.stringify(values);
    const key =
      attempt && attempt.values === fingerprint
        ? attempt.key
        : newIdempotencyKey();
    setAttempt({ key, values: fingerprint });
    setSubmitting(true);
    setError(null);
    try {
      const created = await apiClient().proposeProductCase(values, key);
      void message.success(
        `Đã đề xuất ${created.proposal_code}. Bạn là PIC của hồ sơ này.`,
      );
      form.resetFields();
      setAttempt(null);
      onClose();
      router.push(`/supply-chain/product-cases/${created.id}`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="Đề xuất sản phẩm"
      open={open}
      onCancel={onClose}
      okText="Đề xuất"
      cancelText="Đóng"
      confirmLoading={submitting}
      onOk={() => form.submit()}
      destroyOnHidden
    >
      <Form<ProposeValues>
        form={form}
        layout="vertical"
        validateTrigger="onBlur"
        scrollToFirstError={{ focus: true }}
        onFinish={(values) => void submit(values)}
        onFinishFailed={({ errorFields }) => setInvalid(errorFields.length)}
      >
        {invalid > 0 && (
          <Alert
            type="error"
            showIcon
            title={`Còn ${invalid} trường cần sửa`}
          />
        )}
        <Form.Item
          name="proposalCode"
          label="Mã đề xuất"
          extra="Mã Cung ứng đặt ở bước 1; không trùng trong công ty. Không phải mã hàng."
          rules={[
            { required: true, whitespace: true, message: "Nhập mã đề xuất" },
            { max: 100, message: "Mã đề xuất tối đa 100 ký tự" },
          ]}
        >
          <Input autoComplete="off" />
        </Form.Item>
        <Form.Item
          name="productName"
          label="Tên sản phẩm"
          rules={[
            { required: true, whitespace: true, message: "Nhập tên sản phẩm" },
            { max: 300, message: "Tên sản phẩm tối đa 300 ký tự" },
          ]}
        >
          <Input />
        </Form.Item>
        <Form.Item
          name="category"
          label="Category"
          extra="Ngành hàng của sản phẩm, trong danh sách của công ty. SLA từng bước theo Category này."
          rules={[{ required: true, message: "Chọn Category" }]}
        >
          <Select<string>
            placeholder="Chọn Category"
            loading={categories.loading}
            options={(categories.data ?? []).map((category) => ({
              value: category.key,
              label: category.label,
            }))}
            notFoundContent={
              categories.loading
                ? "Đang tải danh sách Category…"
                : "Công ty chưa có Category nào"
            }
            virtual={false}
          />
        </Form.Item>
        {categories.error != null && (
          // Nothing to choose from is never an empty list to submit past.
          <Alert
            type="error"
            showIcon
            title={`Chưa tải được danh sách Category: ${errorMessage(categories.error)}`}
            action={
              <Button size="small" onClick={categories.reload}>
                Thử lại
              </Button>
            }
          />
        )}
        {error && <Alert type="error" showIcon title={error} />}
      </Form>
    </Modal>
  );
}

function ProductCaseResults({
  state,
  picUserId,
  category,
  filtered,
  onClearFilters,
  onPropose,
}: {
  state?: ProductDevState;
  picUserId?: string;
  category?: string;
  filtered: boolean;
  onClearFilters: () => void;
  onPropose: () => void;
}) {
  const members = useWorkspaceMembers();
  const { principalId } = useAuth();
  const categories = useProductCategories().data;
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [query, setQuery] = useState("");

  const loadFirst = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      const page = await apiClient().listProductCases({
        state,
        picUserId,
        category,
      });
      setList({
        kind: "ready",
        items: [...page.items],
        nextCursor: page.next_cursor,
        loadingMore: false,
        moreError: null,
      });
    } catch (error) {
      setList({ kind: "error", error });
    }
  }, [state, picUserId, category]);

  useEffect(() => {
    void loadFirst();
  }, [loadFirst]);

  const loadMore = async () => {
    if (list.kind !== "ready" || !list.nextCursor || list.loadingMore) return;
    setList({ ...list, loadingMore: true, moreError: null });
    try {
      const page = await apiClient().listProductCases({
        state,
        picUserId,
        category,
        cursor: list.nextCursor,
      });
      setList({
        kind: "ready",
        items: [...list.items, ...page.items],
        nextCursor: page.next_cursor,
        loadingMore: false,
        moreError: null,
      });
    } catch (error) {
      setList({ ...list, loadingMore: false, moreError: errorMessage(error) });
    }
  };

  if (list.kind === "error") {
    return <LoadError error={list.error} onRetry={() => void loadFirst()} />;
  }

  const columns: TableColumnsType<ProductCase> = [
    {
      title: "Hồ sơ",
      key: "case",
      render: (_: unknown, row) => (
        <Flex vertical>
          <Link href={`/supply-chain/product-cases/${row.id}`}>
            <Typography.Text code>{row.proposal_code}</Typography.Text>
          </Link>
          <Typography.Text
            ellipsis={{ tooltip: row.product_name }}
            className="max-w-72"
          >
            {row.product_name}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: "Category",
      dataIndex: "category",
      render: (value: string) => categoryLabel(categories, value),
    },
    {
      title: "NCC",
      dataIndex: "supplier_name",
      render: (value: string | null) => value ?? "Chưa chọn",
    },
    {
      title: "PIC",
      dataIndex: "pic_user_id",
      render: (value: string) =>
        value === principalId ? "Bạn" : memberName(members, value),
    },
    {
      title: "Trạng thái",
      dataIndex: "state",
      render: (value: ProductDevState, row) => (
        <Flex vertical gap={2} align="start">
          <ProductDevStateTag state={value} />
          <Typography.Text type="secondary">
            {row.interrupted_state
              ? `Tạm dừng tại ${PRODUCT_DEV_STATE_LABEL[row.interrupted_state]}`
              : stepLabel(PRODUCT_DEV_STATE_META[value].step)}
          </Typography.Text>
        </Flex>
      ),
    },
    {
      title: "Vòng mẫu",
      dataIndex: "sample_round",
      align: "right",
      render: (value: number) => (value === 0 ? "Chưa có" : value),
    },
    {
      title: `Tạo lúc (${VN_TIME})`,
      dataIndex: "created_at",
      responsive: ["md"],
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  const ready = list.kind === "ready";
  const items = ready ? list.items : [];
  const shown = items.filter((item) =>
    matches(query, [
      item.proposal_code,
      item.product_name,
      categoryLabel(categories, item.category),
    ]),
  );
  return (
    <Flex vertical gap="small">
      {/* Not Input.Search: the list filters as you type, so its button did
          nothing, and it was named "search" in English. */}
      <Input
        type="search"
        allowClear
        prefix={<SearchOutlined aria-hidden />}
        aria-label="Tìm theo mã đề xuất, tên sản phẩm hoặc Category"
        placeholder="Ví dụ: noi inox"
        className="max-w-md"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <Table<ProductCase>
        rowKey="id"
        size="middle"
        pagination={false}
        sticky
        scroll={{ x: "max-content" }}
        loading={!ready}
        columns={columns}
        dataSource={shown}
        locale={{
          emptyText: !ready ? (
            " "
          ) : items.length > 0 ? (
            <Empty
              description={`Không hồ sơ nào trong ${items.length} hồ sơ đã hiện khớp "${query}".`}
            >
              <Button onClick={() => setQuery("")}>Xóa ô tìm</Button>
            </Empty>
          ) : filtered ? (
            <Empty description="Không có hồ sơ nào khớp bộ lọc.">
              <Button onClick={onClearFilters}>Xóa bộ lọc</Button>
            </Empty>
          ) : (
            <Empty description="Chưa có hồ sơ phát triển sản phẩm nào. Cung ứng đề xuất sản phẩm đầu tiên ở đây.">
              <Button type="primary" onClick={onPropose}>
                Đề xuất sản phẩm
              </Button>
            </Empty>
          ),
        }}
      />
      {ready && items.length > 0 && (
        <Flex justify="space-between" align="center" wrap gap="small">
          <Typography.Text role="status">
            {list.nextCursor
              ? `Đã hiện ${items.length} hồ sơ; còn nữa. Ô tìm chỉ tìm trong hồ sơ đã hiện.`
              : `Đã hiện tất cả ${items.length} hồ sơ.`}
          </Typography.Text>
          {list.nextCursor && (
            <Button loading={list.loadingMore} onClick={() => void loadMore()}>
              Tải thêm
            </Button>
          )}
        </Flex>
      )}
      {ready && list.moreError && (
        <Alert type="error" showIcon title={list.moreError} />
      )}
    </Flex>
  );
}
