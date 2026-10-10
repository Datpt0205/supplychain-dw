"use client";

import { useCallback, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Table,
  Tooltip,
  Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { MaskedValue, RegionState } from "@dw/ui";
import type {
  Incoterm,
  POCommercial,
  POPayment,
  PricedLine,
  RedactableAmount,
} from "@dw/api-client";
import { LoadError } from "../load-error";
import {
  calendarDay,
  calendarDayIso,
  formatDate,
  formatDateTimeFull,
} from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { formatCount, formatPrice } from "../../lib/money";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";
import { INCOTERMS, PRICES_HIDDEN } from "./bm04-profile-card";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NO_COMMERCIAL_EDIT =
  "Sửa điều khoản và đơn giá cần quyền sửa dữ liệu thương mại; vai của bạn chưa có.";

const PAYMENT_LABEL: Record<POPayment["kind"], string> = {
  deposit: "Đặt cọc",
  final: "Thanh toán cuối",
};

/** A price cell: the amount, "chưa có" when none is set, or the lock. */
function Amount({
  amount,
  currency,
}: {
  amount: RedactableAmount;
  currency: string | null;
}) {
  if (amount.redacted) return <MaskedValue />;
  if (amount.value === null) return <Typography.Text>chưa có</Typography.Text>;
  return (
    <Typography.Text>{formatPrice(amount.value, currency)}</Typography.Text>
  );
}

type TermsForm = {
  currency: string | null;
  incoterm: Incoterm | null;
  payment_terms: string | null;
  deposit_percent: string | null;
  expected_delivery_date: ReturnType<typeof calendarDay>;
  prices: Record<string, string | null>;
};

/**
 * A PO case's commercial data (ADR 0026): terms, the lines with their unit
 * prices, the total the server computed, and the payments. A caller without
 * the commercial read scope gets every price as a lock (the server left the
 * numbers out); one without the write scope sees the edit button disabled
 * with the reason beside it.
 */
export function POCommercialCard({ caseId }: { caseId: string }) {
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const [form] = Form.useForm<TermsForm>();
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const resource = useCachedResource(
    `supply-chain:po-case:${caseId}:commercial`,
    useCallback(() => apiClient().getPOCommercial(caseId), [caseId]),
  );
  const data = resource.data;

  if (!data) {
    return (
      <Card title="Thương mại">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }

  const lock = !data.can_edit ? NO_COMMERCIAL_EDIT : !online ? OFFLINE : null;
  const currency = data.currency;

  const columns: TableColumnsType<PricedLine> = [
    {
      title: "SKU",
      key: "sku",
      render: (_, line) => line.sku_code ?? line.sku_id,
    },
    {
      title: "Biến thể",
      key: "variant",
      render: (_, line) => line.variant_label ?? "",
    },
    {
      title: "Số lượng",
      key: "quantity",
      align: "right",
      render: (_, line) =>
        line.quantity === null ? "chưa có" : formatCount(line.quantity),
    },
    {
      title: "Đơn giá",
      key: "unit_price",
      align: "right",
      render: (_, line) => (
        <Amount amount={line.unit_price} currency={currency} />
      ),
    },
    {
      title: "Thành tiền",
      key: "line_total",
      align: "right",
      render: (_, line) => (
        <Amount amount={line.line_total} currency={currency} />
      ),
    },
  ];

  const startEdit = () => {
    setRefusal(null);
    form.setFieldsValue({
      currency: data.currency,
      incoterm: data.incoterm,
      payment_terms: data.payment_terms,
      deposit_percent: data.deposit_percent.value,
      expected_delivery_date: calendarDay(data.expected_delivery_date),
      prices: Object.fromEntries(
        data.lines.map((line) => [line.sku_id, line.unit_price.value]),
      ),
    });
    setOpen(true);
  };

  const save = async (values: TermsForm) => {
    setSaving(true);
    setRefusal(null);
    try {
      const body = {
        currency: values.currency?.trim() || null,
        incoterm: values.incoterm ?? null,
        payment_terms: values.payment_terms?.trim() || null,
        deposit_percent: values.deposit_percent ?? null,
        // A calendar day, never a midnight (ui-quality §8).
        expected_delivery_date: calendarDayIso(values.expected_delivery_date),
        line_prices: data.lines.map((line) => ({
          sku_id: line.sku_id,
          unit_price: values.prices?.[line.sku_id] ?? null,
        })),
      };
      await apiClient().setPOCommercial(caseId, body, attemptKey(body));
      message.success("Đã lưu điều khoản thương mại");
      setOpen(false);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Thương mại"
      extra={
        <Flex align="center" gap="small" wrap>
          {lock && <Typography.Text>{lock}</Typography.Text>}
          <Tooltip title={lock ?? undefined}>
            <Button onClick={startEdit} disabled={lock !== null}>
              Sửa điều khoản
            </Button>
          </Tooltip>
        </Flex>
      }
    >
      <Flex vertical gap="middle">
        {!data.prices_visible && (
          <Alert type="info" showIcon title={PRICES_HIDDEN} />
        )}
        <Descriptions
          size="small"
          column={{ xs: 1, md: 2 }}
          items={[
            {
              key: "currency",
              label: "Tiền tệ",
              children: currency ?? "chưa có",
            },
            {
              key: "incoterm",
              label: "Incoterm",
              children: data.incoterm ?? "chưa có",
            },
            {
              key: "terms",
              label: "Điều khoản thanh toán",
              children: data.payment_terms_redacted ? (
                <MaskedValue />
              ) : (
                (data.payment_terms ?? "chưa có")
              ),
            },
            {
              key: "deposit",
              label: "% đặt cọc",
              children: data.deposit_percent.redacted ? (
                <MaskedValue />
              ) : data.deposit_percent.value === null ? (
                "chưa có"
              ) : (
                `${formatPrice(data.deposit_percent.value, null)}%`
              ),
            },
            {
              key: "delivery",
              label: "Ngày giao dự kiến",
              children: data.expected_delivery_date
                ? formatDate(data.expected_delivery_date)
                : "chưa có",
            },
            {
              key: "total",
              label: "Tổng (hệ thống tính)",
              children: (
                <Amount amount={data.order_total} currency={currency} />
              ),
            },
          ]}
        />
        <Table<PricedLine>
          rowKey="sku_id"
          size="small"
          columns={columns}
          dataSource={data.lines}
          pagination={false}
          scroll={{ x: "max-content" }}
          locale={{ emptyText: "Hồ sơ chưa có dòng hàng." }}
        />
        {data.payments.length > 0 && (
          <Descriptions
            size="small"
            column={1}
            title="Thanh toán"
            items={data.payments.map((payment) => ({
              key: payment.id,
              label: `${PAYMENT_LABEL[payment.kind]} (bản ${payment.version})`,
              children: (
                <Flex gap="small" wrap>
                  <Amount amount={payment.amount} currency={payment.currency} />
                  {payment.due_date && (
                    <Typography.Text>
                      hạn {formatDate(payment.due_date)}
                    </Typography.Text>
                  )}
                  {payment.paid_on && (
                    <Typography.Text>
                      đã trả {formatDate(payment.paid_on)}
                    </Typography.Text>
                  )}
                  <Typography.Text type="secondary">
                    ghi lúc {formatDateTimeFull(payment.recorded_at)}
                  </Typography.Text>
                </Flex>
              ),
            }))}
          />
        )}
      </Flex>
      <Modal
        open={open}
        title="Sửa điều khoản thương mại"
        okText="Lưu"
        cancelText="Hủy"
        onOk={() => form.submit()}
        onCancel={() => setOpen(false)}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form<TermsForm> form={form} layout="vertical" onFinish={save}>
          {refusal && <Alert type="error" showIcon title={refusal} />}
          <Form.Item name="currency" label="Tiền tệ (mã ISO 4217)">
            <Input maxLength={3} />
          </Form.Item>
          <Form.Item name="incoterm" label="Incoterm">
            <Select
              allowClear
              options={INCOTERMS.map((t) => ({ value: t, label: t }))}
            />
          </Form.Item>
          <Form.Item name="payment_terms" label="Điều khoản thanh toán">
            <Input.TextArea maxLength={500} autoSize={{ minRows: 2 }} />
          </Form.Item>
          <Form.Item name="deposit_percent" label="% đặt cọc">
            <InputNumber<string>
              stringMode
              min="0"
              max="100"
              className="w-full"
            />
          </Form.Item>
          <Form.Item name="expected_delivery_date" label="Ngày giao dự kiến">
            <DatePicker format="DD/MM/YYYY" className="w-full" />
          </Form.Item>
          {data.lines.map((line) => (
            <Form.Item
              key={line.sku_id}
              name={["prices", line.sku_id]}
              label={`Đơn giá ${line.sku_code ?? line.sku_id}`}
            >
              <InputNumber<string> stringMode min="0" className="w-full" />
            </Form.Item>
          ))}
        </Form>
      </Modal>
    </Card>
  );
}
