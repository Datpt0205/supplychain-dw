"use client";

import { useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import {
  ApiError,
  type ProductCaseDetail,
  type ProductCaseStepInput,
} from "@dw/api-client";
import type { ProductAction, ProductDevState, Sku } from "@dw/contracts";
import {
  PRODUCT_ACTION_LABEL,
  PRODUCT_DEV_STATE_LABEL,
  dutyLock,
  unmetLock,
} from "./product-case-labels";
import { useAuth } from "../../lib/auth/auth-context";
import { formatCount } from "../../lib/money";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { apiClient } from "../../lib/session";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";

/** The step-9 coding steps this card takes; the page's step list leaves them
 * to it. */
export const CODING_ACTIONS: readonly ProductAction[] = [
  "issue_item_code",
  "add_sku",
  "remove_sku",
];

// The constraints a taken code is refused by (ADR 0018): the server names
// them in a 409's details, and the page shows the sentence at the field.
const ITEM_CODE_TAKEN = "uq_item_codes_tenant_id_code";
const SKU_CODE_TAKEN = "uq_skus_tenant_id_sku_code";

/** Whether the case is at or past step 9, where its codes are shown. */
export function showsCoding(detail: ProductCaseDetail): boolean {
  const states: ProductDevState[] = [
    "item_coding",
    "pending_signoff",
    "ready_to_order",
  ];
  return (
    states.includes(detail.state) ||
    (detail.interrupted_state !== null &&
      states.includes(detail.interrupted_state)) ||
    detail.item_code !== null
  );
}

/** Why the coding steps are not offered from where the case is. */
function stateLock(detail: ProductCaseDetail): string {
  switch (detail.state) {
    case "pending_signoff":
      return "Hồ sơ đang chờ ký; mã hàng và SKU khóa tới khi có kết quả ký.";
    case "ready_to_order":
      return "Đã ký đủ; mã hàng và SKU đã chốt.";
    case "ordered":
      return "Đã đặt hàng; mã hàng và SKU đã chốt.";
    case "waiting_external":
    case "blocked":
    case "manual_review":
      return "Hồ sơ đang tạm dừng; tiếp tục hồ sơ để sửa mã hàng và SKU.";
    case "cancelled":
      return "Hồ sơ đã hủy; mã hàng và SKU giữ nguyên, không sửa được.";
    default:
      return `Chỉ sửa được ở bước ${PRODUCT_DEV_STATE_LABEL.item_coding}.`;
  }
}

/** The 409 a taken code comes back as, when it is that and not another. */
function takenBy(error: unknown, constraint: string): string | null {
  return error instanceof ApiError &&
    error.status === 409 &&
    error.body.details.constraint === constraint
    ? error.body.message
    : null;
}

/**
 * Step 9: the official item code and its SKUs (ADR 0018). Each control comes
 * from a step the server offers, with the scope its duty needs and what the
 * case still lacks (`unmet`); a control the viewer may not use is drawn
 * locked with the reason in words beside it. Whether a code is free in the
 * company is the server's answer only: a taken code is shown at its field.
 */
export function ItemCodingCard({
  detail,
  onStep,
}: {
  detail: ProductCaseDetail;
  onStep: () => void;
}) {
  const { hasScope } = useAuth();
  const online = useOnline();
  const { message, modal } = App.useApp();
  const [codeForm] = Form.useForm<{ code: string }>();
  const [skuForm] = Form.useForm<{
    skuCode: string;
    variantLabel: string;
    plannedQuantity?: number | null;
  }>();
  const [busy, setBusy] = useState<ProductAction | null>(null);
  const [error, setError] = useState<string | null>(null);
  const attemptKey = useAttemptKey();

  const lockOf = (action: ProductAction): string | null => {
    const option = detail.actions.find((o) => o.action === action);
    if (!option) return stateLock(detail);
    if (!hasScope(option.required_scope))
      return dutyLock(option.required_scope);
    if (option.unmet.length > 0) return unmetLock(option.unmet);
    return online ? null : OFFLINE;
  };

  const take = async (input: ProductCaseStepInput): Promise<boolean> => {
    setBusy(input.action);
    setError(null);
    try {
      await apiClient().takeProductCaseStep(
        detail.id,
        input,
        attemptKey(input),
      );
      void message.success(`Đã ghi: ${PRODUCT_ACTION_LABEL[input.action]}.`);
      onStep();
      return true;
    } catch (caught) {
      const taken =
        takenBy(caught, ITEM_CODE_TAKEN) ?? takenBy(caught, SKU_CODE_TAKEN);
      if (taken && input.action === "issue_item_code") {
        codeForm.setFields([{ name: "code", errors: [taken] }]);
      } else if (taken && input.action === "add_sku") {
        skuForm.setFields([{ name: "skuCode", errors: [taken] }]);
      } else {
        setError(errorMessage(caught));
      }
      return false;
    } finally {
      setBusy(null);
    }
  };

  const issueLock = lockOf("issue_item_code");
  const addLock = lockOf("add_sku");
  const removeLock = lockOf("remove_sku");
  const issueLabel = detail.item_code ? "Sửa mã hàng" : "Cấp mã hàng";

  const confirmRemove = (sku: Sku) => {
    modal.confirm({
      title: `Bỏ SKU ${sku.sku_code}?`,
      content: `${sku.variant_label}. Mã SKU này vẫn được giữ cho công ty.`,
      okText: "Bỏ SKU",
      cancelText: "Giữ lại",
      okButtonProps: { danger: true },
      autoFocusButton: "cancel",
      onOk: () => take({ action: "remove_sku", skuId: sku.id }),
    });
  };

  const columns: TableColumnsType<Sku> = [
    {
      title: "Mã SKU",
      dataIndex: "sku_code",
      render: (value: string) => (
        <Typography.Text code>{value}</Typography.Text>
      ),
    },
    { title: "Biến thể", dataIndex: "variant_label" },
    {
      title: "Số lượng dự kiến",
      dataIndex: "planned_quantity",
      align: "right",
      render: (value: number | null) =>
        value === null ? "—" : formatCount(value),
    },
    {
      title: "",
      key: "remove",
      align: "right",
      render: (_: unknown, sku: Sku) => {
        const button = (
          <Button
            danger
            type="link"
            disabled={removeLock !== null}
            loading={busy === "remove_sku"}
            onClick={() => confirmRemove(sku)}
          >
            Bỏ
          </Button>
        );
        return removeLock ? (
          <Tooltip title={removeLock}>
            <span>{button}</span>
          </Tooltip>
        ) : (
          button
        );
      },
    },
  ];

  return (
    <Card title="Mã hàng và SKU (bước 9)">
      <Flex vertical gap="middle">
        <Flex vertical gap="small">
          <Typography.Text strong>Mã hàng chính thức</Typography.Text>
          {detail.item_code ? (
            <Typography.Text code copyable>
              {detail.item_code.code}
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">Chưa có mã hàng.</Typography.Text>
          )}
          {issueLock === null ? (
            <Form<{ code: string }>
              form={codeForm}
              layout="inline"
              validateTrigger="onBlur"
              onFinish={(values) =>
                void take({ action: "issue_item_code", itemCode: values.code })
              }
            >
              <Form.Item
                name="code"
                label={detail.item_code ? "Mã hàng mới" : "Mã hàng"}
                rules={[
                  { required: true, whitespace: true, message: "Nhập mã hàng" },
                  { max: 100, message: "Mã hàng tối đa 100 ký tự" },
                ]}
              >
                <Input autoComplete="off" />
              </Form.Item>
              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  loading={busy === "issue_item_code"}
                >
                  {issueLabel}
                </Button>
              </Form.Item>
            </Form>
          ) : (
            <Typography.Text>
              {issueLabel}: {issueLock}
            </Typography.Text>
          )}
        </Flex>

        <Table<Sku>
          rowKey="id"
          size="small"
          pagination={false}
          scroll={{ x: "max-content" }}
          columns={columns}
          dataSource={detail.skus}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="Chưa có SKU nào. SKU thêm được sau khi có mã hàng chính thức."
              />
            ),
          }}
        />
        {removeLock !== null && detail.skus.length > 0 && (
          <Typography.Text>Bỏ SKU: {removeLock}</Typography.Text>
        )}

        {addLock === null ? (
          <Form
            form={skuForm}
            layout="inline"
            validateTrigger="onBlur"
            scrollToFirstError={{ focus: true }}
            onFinish={(values) =>
              void take({
                action: "add_sku",
                sku: {
                  skuCode: values.skuCode,
                  variantLabel: values.variantLabel,
                  plannedQuantity: values.plannedQuantity ?? undefined,
                },
              }).then((done) => {
                if (done) skuForm.resetFields();
              })
            }
          >
            <Form.Item
              name="skuCode"
              label="Mã SKU"
              rules={[
                { required: true, whitespace: true, message: "Nhập mã SKU" },
                { max: 100, message: "Mã SKU tối đa 100 ký tự" },
              ]}
            >
              <Input autoComplete="off" />
            </Form.Item>
            <Form.Item
              name="variantLabel"
              label="Biến thể"
              rules={[
                { required: true, whitespace: true, message: "Nhập biến thể" },
                { max: 200, message: "Tối đa 200 ký tự" },
              ]}
            >
              <Input placeholder="Ví dụ: Đỏ, 24cm" />
            </Form.Item>
            <Form.Item name="plannedQuantity" label="Số lượng dự kiến">
              <InputNumber min={1} precision={0} />
            </Form.Item>
            <Form.Item>
              <Button htmlType="submit" loading={busy === "add_sku"}>
                Thêm SKU
              </Button>
            </Form.Item>
          </Form>
        ) : (
          <Typography.Text>Thêm SKU: {addLock}</Typography.Text>
        )}
        {error && <Alert type="error" showIcon title={error} />}
      </Flex>
    </Card>
  );
}
