"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Flex,
  Form,
  Input,
  InputNumber,
  Select,
  Switch,
  Tooltip,
  Typography,
} from "antd";
import { MaskedValue, RegionState } from "@dw/ui";
import type { Bm04Field, Incoterm, ProductProfile } from "@dw/api-client";
import { LoadError } from "../load-error";
import { formatDateTimeFull } from "../../lib/dates";
import { errorMessage } from "../../lib/error-message";
import { useOnline } from "../../lib/hooks/use-online";
import { useAttemptKey } from "../../lib/idempotency-key";
import { formatPrice } from "../../lib/money";
import { apiClient } from "../../lib/session";
import { useCachedResource } from "../../lib/use-cached-resource";

const OFFLINE = "Không có kết nối mạng. Kết nối lại rồi thử lại.";
export const NO_EDIT = "Bạn chưa có quyền sửa hồ sơ phát triển sản phẩm.";
export const NO_PRICE_EDIT =
  "Giá và tiền tệ cần quyền sửa dữ liệu thương mại; các ô khác vẫn lưu được.";
/** Said once on the region; each hidden cell is a `MaskedValue`. */
export const PRICES_HIDDEN =
  "Giá chỉ hiện với người có quyền xem dữ liệu thương mại.";

export const INCOTERMS: readonly Incoterm[] = [
  "EXW",
  "FCA",
  "CPT",
  "CIP",
  "DAP",
  "DPU",
  "DDP",
  "FAS",
  "FOB",
  "CFR",
  "CIF",
];

type FormValues = {
  attributes: Record<string, unknown>;
  moq: number | null;
  lead_time_days: number | null;
  incoterm: Incoterm | null;
  unit_price: string | null;
  currency: string | null;
};

function attributeInput(field: Bm04Field, disabled: boolean) {
  switch (field.kind) {
    case "number":
      return (
        <InputNumber
          disabled={disabled}
          className="w-full"
          addonAfter={field.unit ?? undefined}
        />
      );
    case "integer":
      return (
        <InputNumber
          disabled={disabled}
          precision={0}
          className="w-full"
          addonAfter={field.unit ?? undefined}
        />
      );
    case "boolean":
      return <Switch disabled={disabled} />;
    case "choice":
      return (
        <Select
          disabled={disabled}
          allowClear
          options={(field.options ?? []).map((o) => ({ value: o, label: o }))}
        />
      );
    default:
      return (field.max_length ?? 2000) > 300 ? (
        <Input.TextArea
          disabled={disabled}
          maxLength={field.max_length ?? undefined}
          autoSize={{ minRows: 2, maxRows: 6 }}
        />
      ) : (
        <Input disabled={disabled} maxLength={field.max_length ?? undefined} />
      );
  }
}

/**
 * BM04 as a form (ADR 0026, step 7): the tenant's schema drives the fields,
 * each save is a new version. Price and currency sit behind the commercial
 * scope: hidden as "Ẩn" (never 0) to a reader without it, locked with the
 * reason to a writer without it — the server already left them out and keeps
 * the last price on such a save. Uploading a BM04 file stays possible in the
 * documents card.
 */
export function Bm04ProfileCard({ caseId }: { caseId: string }) {
  const { message } = App.useApp();
  const online = useOnline();
  const attemptKey = useAttemptKey();
  const [form] = Form.useForm<FormValues>();
  const [saving, setSaving] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const resource = useCachedResource(
    `supply-chain:product-case:${caseId}:profile`,
    useCallback(() => apiClient().getProductProfile(caseId), [caseId]),
  );
  const profile = resource.data;

  useEffect(() => {
    if (!profile) return;
    form.setFieldsValue({
      attributes: profile.attributes,
      moq: profile.moq,
      lead_time_days: profile.lead_time_days,
      incoterm: profile.incoterm,
      unit_price: profile.unit_price.value,
      currency: profile.currency,
    });
  }, [profile, form]);

  if (!profile) {
    return (
      <Card title="BM04 — Hồ sơ sản phẩm (bước 7)">
        {resource.error != null ? (
          <LoadError error={resource.error} onRetry={resource.reload} compact />
        ) : resource.loading ? (
          <RegionState kind="loading" compact />
        ) : null}
      </Card>
    );
  }

  const locked = !profile.can_edit;
  const pricesLocked = locked || !profile.can_edit_prices;

  const save = async (values: FormValues) => {
    setSaving(true);
    setRefusal(null);
    try {
      const body = {
        attributes: values.attributes ?? {},
        moq: values.moq ?? null,
        lead_time_days: values.lead_time_days ?? null,
        incoterm: values.incoterm ?? null,
        // Absent unless this person may set prices: the server then keeps
        // the last version's price rather than reading a blank as "none".
        ...(profile.can_edit_prices
          ? {
              prices: {
                unit_price: values.unit_price ?? null,
                currency: values.currency?.trim() || null,
              },
            }
          : {}),
      };
      const saved = await apiClient().saveProductProfile(
        caseId,
        body,
        attemptKey(body),
      );
      message.success(`Đã lưu BM04 phiên bản ${saved.version ?? ""}`);
      resource.reload();
    } catch (error) {
      setRefusal(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="BM04 — Hồ sơ sản phẩm (bước 7)"
      extra={
        profile.version !== null ? (
          <Typography.Text type="secondary">
            Phiên bản {profile.version} ·{" "}
            {formatDateTimeFull(profile.created_at)}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">Chưa lưu lần nào</Typography.Text>
        )
      }
    >
      <Flex vertical gap="middle">
        {locked && <Alert type="info" showIcon title={NO_EDIT} />}
        {refusal && <Alert type="error" showIcon title={refusal} />}
        <Form<FormValues>
          form={form}
          layout="vertical"
          onFinish={save}
          disabled={locked}
        >
          {profile.bm04_schema.fields.map((field) => (
            <Form.Item
              key={field.key}
              name={["attributes", field.key]}
              label={field.label}
              valuePropName={field.kind === "boolean" ? "checked" : "value"}
              rules={
                field.required
                  ? [{ required: true, message: `Cần ${field.label}` }]
                  : []
              }
            >
              {attributeInput(field, locked)}
            </Form.Item>
          ))}
          <Flex gap="middle" wrap>
            <Form.Item name="moq" label="MOQ" className="min-w-40">
              <InputNumber precision={0} min={1} className="w-full" />
            </Form.Item>
            <Form.Item
              name="lead_time_days"
              label="Thời gian sản xuất (ngày)"
              className="min-w-40"
            >
              <InputNumber precision={0} min={0} className="w-full" />
            </Form.Item>
            <Form.Item name="incoterm" label="Incoterm" className="min-w-40">
              <Select
                allowClear
                options={INCOTERMS.map((t) => ({ value: t, label: t }))}
              />
            </Form.Item>
          </Flex>
          {profile.prices_visible ? (
            <Flex vertical>
              <Flex gap="middle" wrap>
                <Form.Item
                  name="unit_price"
                  label="Đơn giá"
                  className="min-w-40"
                >
                  <InputNumber<string>
                    stringMode
                    min="0"
                    disabled={pricesLocked}
                    className="w-full"
                  />
                </Form.Item>
                <Form.Item name="currency" label="Tiền tệ" className="min-w-28">
                  <Input maxLength={3} disabled={pricesLocked} />
                </Form.Item>
              </Flex>
              {pricesLocked && !locked && (
                <Typography.Text>{NO_PRICE_EDIT}</Typography.Text>
              )}
            </Flex>
          ) : (
            <Flex vertical gap="small">
              <Alert type="info" showIcon title={PRICES_HIDDEN} />
              <Descriptions
                size="small"
                column={1}
                items={[
                  { key: "price", label: "Đơn giá", children: <MaskedValue /> },
                ]}
              />
            </Flex>
          )}
          {!locked && (
            <Tooltip title={online ? undefined : OFFLINE}>
              <Button
                type="primary"
                htmlType="submit"
                loading={saving}
                disabled={!online}
              >
                Lưu phiên bản mới
              </Button>
            </Tooltip>
          )}
        </Form>
        {profile.prices_visible && profile.unit_price.value !== null && (
          <Typography.Text type="secondary">
            Giá đang lưu:{" "}
            {formatPrice(profile.unit_price.value, profile.currency)}
          </Typography.Text>
        )}
      </Flex>
    </Card>
  );
}
