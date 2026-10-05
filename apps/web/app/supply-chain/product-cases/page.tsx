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
  Result,
  Select,
  Skeleton,
  Switch,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  productDevStateSchema,
  type ProductCase,
  type ProductDevState,
} from "@dw/contracts";
import {
  PRODUCT_DEV_STATE_LABEL,
  ProductDevStateTag,
  dutyLock,
  dutyScope,
} from "../../../components/supply-chain/product-case-labels";
import { useAuth } from "../../../lib/auth/auth-context";
import { formatDateTime } from "../../../lib/dates";
import { memberName, useWorkspaceMembers } from "../../../lib/directory";
import { errorCode, errorMessage } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { newIdempotencyKey } from "../../../lib/idempotency-key";
import { apiClient } from "../../../lib/session";

/**
 * Hồ sơ phát triển sản phẩm: every product proposed in this workspace, newest
 * first, narrowed by state or by "mine" (the PIC filter only narrows: everyone
 * with the read sees every case, QE-18). The filter lives in the URL, so a
 * filtered view can be sent to a colleague. Proposing opens a short form; the
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

interface ListFilter {
  state?: ProductDevState;
  mine: boolean;
}

function readFilter(params: URLSearchParams): ListFilter {
  return {
    state: productDevStateSchema.safeParse(params.get("state")).data,
    mine: params.get("mine") === "1",
  };
}

function hrefFor(filter: ListFilter): string {
  const search = new URLSearchParams();
  if (filter.state) search.set("state", filter.state);
  if (filter.mine) search.set("mine", "1");
  const rendered = search.toString();
  return `/supply-chain/product-cases${rendered ? `?${rendered}` : ""}`;
}

const STATE_OPTIONS = productDevStateSchema.options.map((value) => ({
  value,
  label: PRODUCT_DEV_STATE_LABEL[value],
}));

type ListState =
  | { kind: "loading" }
  | { kind: "error"; message: string; code: string | null }
  | {
      kind: "ready";
      items: ProductCase[];
      nextCursor: string | null;
      loadingMore: boolean;
      moreError: string | null;
    };

function ProductCasesView() {
  const searchParams = useSearchParams();
  const filter = readFilter(searchParams);
  const { principalId } = useAuth();
  const [proposing, setProposing] = useState(false);

  // The URL is the filter's only home, so back/forward and a shared link land
  // on the same view; `replaceState` keeps Next's params in sync without a
  // server round trip.
  const apply = (next: ListFilter) => {
    window.history.replaceState(null, "", hrefFor(next));
  };
  const filtered = filter.state !== undefined || filter.mine;

  return (
    <div className="mx-auto max-w-6xl">
      <Flex vertical gap="middle">
        <Flex justify="space-between" align="start" wrap gap="middle">
          <div>
            <Typography.Title level={3}>
              Hồ sơ phát triển sản phẩm
            </Typography.Title>
            <Typography.Paragraph>
              Sản phẩm Cung ứng đề xuất ở bước 1, qua lấy mẫu và test mẫu tới
              khi chờ BGĐ duyệt.
            </Typography.Paragraph>
          </div>
          <ProposeButton onOpen={() => setProposing(true)} />
        </Flex>

        <Flex wrap gap="middle" align="center">
          <Select<ProductDevState | "">
            aria-label="Lọc theo trạng thái"
            className="min-w-56"
            value={filter.state ?? ""}
            options={[
              { value: "", label: "Tất cả trạng thái" },
              ...STATE_OPTIONS,
            ]}
            onChange={(value) =>
              apply({ ...filter, state: value === "" ? undefined : value })
            }
            virtual={false}
          />
          <Flex gap="small" align="center">
            <Switch
              id="product-cases-mine"
              checked={filter.mine}
              disabled={principalId === null}
              onChange={(checked) => apply({ ...filter, mine: checked })}
            />
            <label htmlFor="product-cases-mine">Chỉ hồ sơ tôi là PIC</label>
          </Flex>
        </Flex>

        {/* Keyed on the filter: a new filter is a fresh list, never page two
            of the old one spliced on. */}
        <ProductCaseResults
          key={JSON.stringify([filter.state, filter.mine, principalId])}
          state={filter.state}
          picUserId={filter.mine ? (principalId ?? undefined) : undefined}
          filtered={filtered}
          onClearFilters={() => apply({ mine: false })}
          onPropose={() => setProposing(true)}
        />
      </Flex>
      <ProposeModal open={proposing} onClose={() => setProposing(false)} />
    </div>
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
            message={`Còn ${invalid} trường cần sửa`}
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
          extra="Ngành hàng của sản phẩm; danh sách chọn có từ S6 (ADR 0019)."
          rules={[
            { required: true, whitespace: true, message: "Nhập Category" },
            { max: 100, message: "Category tối đa 100 ký tự" },
          ]}
        >
          <Input />
        </Form.Item>
        {error && <Alert type="error" showIcon message={error} />}
      </Form>
    </Modal>
  );
}

function ProductCaseResults({
  state,
  picUserId,
  filtered,
  onClearFilters,
  onPropose,
}: {
  state?: ProductDevState;
  picUserId?: string;
  filtered: boolean;
  onClearFilters: () => void;
  onPropose: () => void;
}) {
  const members = useWorkspaceMembers();
  const { principalId } = useAuth();
  const [list, setList] = useState<ListState>({ kind: "loading" });

  const loadFirst = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      const page = await apiClient().listProductCases({ state, picUserId });
      setList({
        kind: "ready",
        items: [...page.items],
        nextCursor: page.next_cursor,
        loadingMore: false,
        moreError: null,
      });
    } catch (error) {
      setList({
        kind: "error",
        message: errorMessage(error),
        code: errorCode(error),
      });
    }
  }, [state, picUserId]);

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
    return list.code === "permission_denied" ? (
      <Result
        status="403"
        title="Bạn chưa được xem hồ sơ phát triển sản phẩm"
        subTitle="Cần một vai Supply Chain có quyền xem hồ sơ (supply_chain.product_case.read). Liên hệ quản trị workspace."
      />
    ) : (
      <Alert
        type="error"
        showIcon
        message="Không tải được danh sách hồ sơ"
        description={list.message}
        action={
          <Button
            size="small"
            icon={<ReloadOutlined aria-hidden />}
            onClick={() => void loadFirst()}
          >
            Thử lại
          </Button>
        }
      />
    );
  }

  const columns: TableColumnsType<ProductCase> = [
    {
      title: "Mã đề xuất",
      dataIndex: "proposal_code",
      render: (value: string, row) => (
        <Link href={`/supply-chain/product-cases/${row.id}`}>{value}</Link>
      ),
    },
    {
      title: "Sản phẩm",
      dataIndex: "product_name",
      render: (value: string) => (
        <Typography.Text ellipsis={{ tooltip: value }} className="max-w-72">
          {value}
        </Typography.Text>
      ),
    },
    { title: "Category", dataIndex: "category" },
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
        <Flex wrap gap="small">
          <ProductDevStateTag state={value} />
          {row.interrupted_state && (
            <Typography.Text>
              tạm dừng tại {PRODUCT_DEV_STATE_LABEL[row.interrupted_state]}
            </Typography.Text>
          )}
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
      title: "Tạo lúc",
      dataIndex: "created_at",
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  const ready = list.kind === "ready";
  const items = ready ? list.items : [];
  return (
    <Flex vertical gap="small">
      <Table<ProductCase>
        rowKey="id"
        size="middle"
        pagination={false}
        sticky
        scroll={{ x: "max-content" }}
        loading={!ready}
        columns={columns}
        dataSource={items}
        locale={{
          emptyText: !ready ? (
            " "
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
              ? `Đã hiện ${items.length} hồ sơ; còn nữa.`
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
        <Alert type="error" showIcon message={list.moreError} />
      )}
    </Flex>
  );
}
