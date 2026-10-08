"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  InputNumber,
  Select,
  Switch,
  Table,
  Tag,
  Tooltip,
  Typography,
  type TableColumnsType,
} from "antd";
import type {
  PackagingPolicy,
  SLAConfirmationStatus,
  SLAMilestone,
  SLAPolicy,
} from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { LoadError } from "../../../components/load-error";
import { supplyChainCrumbs } from "../../../components/supply-chain/crumbs";
import { milestoneLabel } from "../../../components/supply-chain/sla-status-badge";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage } from "../../../lib/error-message";
import { useOnline } from "../../../lib/hooks/use-online";
import { roleLabel } from "../../../lib/nav/roles";
import { apiClient } from "../../../lib/session";
import { useCachedResource } from "../../../lib/use-cached-resource";

// The scopes the two PUT routes check; the server refuses without them, and
// these only decide what the screen offers.
const SLA_POLICY_READ = "supply_chain.sla_policy.read";
const SLA_POLICY_WRITE = "supply_chain.sla_policy.write";
const ACTION_DUTIES_READ = "supply_chain.action_duties.read";
const ACTION_DUTIES_WRITE = "supply_chain.action_duties.write";

const PROCESS_ADMIN = roleLabel("sc_process_admin");
const SLA_LOCK = `Chỉ xem: sửa SLA cần quyền ${SLA_POLICY_WRITE} (vai ${PROCESS_ADMIN}).`;
const PACKAGING_LOCK = `Chỉ xem: đổi quy tắc này cần quyền ${ACTION_DUTIES_WRITE} (vai ${PROCESS_ADMIN}).`;
const OFFLINE_LOCK = "Đang mất kết nối: lưu lại khi có mạng.";

const STATUS_LABEL: Record<SLAConfirmationStatus, string> = {
  confirmed: "Đang áp dụng",
  pending_business_confirmation: "Chờ khách xác nhận (không đánh giá)",
};

const days = (duration: string): number => Number.parseInt(duration, 10);
const asDuration = (value: number): string =>
  `${Math.max(1, Math.round(value))}d`;

/**
 * The tenant's own SLA policy and step 13's packaging rule, read and replaced
 * whole through the existing `PUT /sla-policy` and `PUT /packaging-policy`.
 * Without the write scope every control is locked and says why; the server
 * refuses the write either way (hiding a control is not authorization).
 */
export default function SupplyChainSettingsPage() {
  const { hasScope } = useAuth();
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        breadcrumb={supplyChainCrumbs("Cấu hình")}
        title="Cấu hình Supply Chain"
        subtitle="SLA theo Category và quy tắc test tiền sản xuất của công ty. Số ở đây là của công ty bạn, ghi đè số mặc định của nền tảng."
      />
      <Flex vertical gap="middle">
        {hasScope(SLA_POLICY_READ) ? (
          <SlaCard canWrite={hasScope(SLA_POLICY_WRITE)} />
        ) : (
          <Card title="SLA theo Category">
            <RegionState kind="forbidden" compact />
          </Card>
        )}
        {hasScope(ACTION_DUTIES_READ) ? (
          <PackagingCard canWrite={hasScope(ACTION_DUTIES_WRITE)} />
        ) : (
          <Card title="Test tiền sản xuất (bước 12–13)">
            <RegionState kind="forbidden" compact />
          </Card>
        )}
      </Flex>
    </div>
  );
}

interface MilestoneRow {
  key: string;
  milestone: string;
  value: SLAMilestone;
}

function rowsOf(milestones: Record<string, SLAMilestone>): MilestoneRow[] {
  return Object.entries(milestones).map(([milestone, value]) => ({
    key: milestone,
    milestone,
    value,
  }));
}

function SlaCard({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp();
  const online = useOnline();
  const resource = useCachedResource(
    "supply-chain:sla-policy",
    useCallback(() => apiClient().getSLAPolicy(), []),
  );
  const [draft, setDraft] = useState<SLAPolicy | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (resource.data) setDraft(resource.data);
  }, [resource.data]);

  const lock = !canWrite ? SLA_LOCK : !online ? OFFLINE_LOCK : null;
  const changed =
    draft !== null && JSON.stringify(draft) !== JSON.stringify(resource.data);

  const setMilestone = (
    category: string | null,
    milestone: string,
    next: Partial<SLAMilestone>,
  ) =>
    setDraft((current) => {
      if (!current) return current;
      const own =
        category === null ? current.default : current.by_category[category];
      const base = own?.[milestone];
      if (!own || !base) return current;
      const updated = { ...own, [milestone]: { ...base, ...next } };
      return category === null
        ? { ...current, default: updated }
        : {
            ...current,
            by_category: { ...current.by_category, [category]: updated },
          };
    });

  const columns = (category: string | null): TableColumnsType<MilestoneRow> => [
    {
      title: "Mốc",
      dataIndex: "milestone",
      render: (milestone: string) => (
        <Typography.Text strong>{milestoneLabel(milestone)}</Typography.Text>
      ),
    },
    {
      title: "Số ngày",
      width: 140,
      render: (_: unknown, row) => (
        <InputNumber
          aria-label={`Số ngày mốc ${milestoneLabel(row.milestone)}`}
          min={1}
          max={365}
          value={days(row.value.duration)}
          disabled={lock !== null}
          onChange={(value) =>
            value !== null &&
            setMilestone(category, row.milestone, {
              duration: asDuration(value),
            })
          }
        />
      ),
    },
    {
      title: "Trạng thái",
      width: 280,
      render: (_: unknown, row) => (
        <Select<SLAConfirmationStatus>
          aria-label={`Trạng thái mốc ${milestoneLabel(row.milestone)}`}
          className="w-full"
          value={row.value.status}
          disabled={lock !== null}
          options={Object.entries(STATUS_LABEL).map(([value, label]) => ({
            value: value as SLAConfirmationStatus,
            label,
          }))}
          onChange={(status) =>
            setMilestone(category, row.milestone, { status })
          }
        />
      ),
    },
    {
      title: "Mô tả",
      dataIndex: ["value", "description"],
      responsive: ["md"],
      render: (description: string) => (
        <Typography.Text type="secondary">{description || "—"}</Typography.Text>
      ),
    },
  ];

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      await apiClient().setSLAPolicy(draft);
      message.success("Đã lưu SLA của công ty.");
      resource.reload();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  if (!draft) {
    return (
      <Card title="SLA theo Category">
        {resource.error != null ? (
          <LoadError compact error={resource.error} onRetry={resource.reload} />
        ) : (
          <RegionState kind="loading" compact />
        )}
      </Card>
    );
  }

  const labelOf = (key: string) =>
    draft.categories.find((category) => category.key === key)?.label ?? key;
  const saveButton = (
    <Button
      type="primary"
      onClick={() => void save()}
      loading={saving}
      disabled={lock !== null || !changed}
    >
      Lưu SLA
    </Button>
  );

  return (
    <Card
      title="SLA theo Category"
      extra={<Tag>policy {draft.policy_version}</Tag>}
    >
      <Flex vertical gap="middle">
        {lock && <Alert type="info" showIcon message={lock} />}
        <Flex wrap gap="small" align="center">
          <Typography.Text>Category:</Typography.Text>
          {draft.categories.map((category) => (
            <Tag key={category.key}>{category.label}</Tag>
          ))}
          <Typography.Text type="secondary">
            Thêm hay bỏ Category chưa làm trên màn này.
          </Typography.Text>
        </Flex>
        <Typography.Title level={5}>Số chung mọi Category</Typography.Title>
        <Table<MilestoneRow>
          size="small"
          pagination={false}
          rowKey="key"
          columns={columns(null)}
          dataSource={rowsOf(draft.default)}
        />
        {Object.entries(draft.by_category).map(([category, milestones]) => (
          <Flex vertical gap="small" key={category}>
            <Typography.Title level={5}>
              Riêng Category {labelOf(category)}
            </Typography.Title>
            <Table<MilestoneRow>
              size="small"
              pagination={false}
              rowKey="key"
              columns={columns(category)}
              dataSource={rowsOf(milestones)}
            />
          </Flex>
        ))}
        <Typography.Title level={5}>Nhắc NCC cập nhật</Typography.Title>
        <Flex wrap gap="middle" align="center">
          <label>
            <Typography.Text>Nhắc sau </Typography.Text>
            <InputNumber
              aria-label="Nhắc NCC sau số ngày"
              min={1}
              max={365}
              value={days(draft.supplier_update.reminder_after)}
              disabled={lock !== null}
              onChange={(value) =>
                value !== null &&
                setDraft({
                  ...draft,
                  supplier_update: {
                    ...draft.supplier_update,
                    reminder_after: asDuration(value),
                  },
                })
              }
            />
            <Typography.Text> ngày</Typography.Text>
          </label>
          <label>
            <Typography.Text>Leo thang sau </Typography.Text>
            <InputNumber
              aria-label="Leo thang sau số ngày"
              min={1}
              max={365}
              value={days(draft.supplier_update.escalation_after)}
              disabled={lock !== null}
              onChange={(value) =>
                value !== null &&
                setDraft({
                  ...draft,
                  supplier_update: {
                    ...draft.supplier_update,
                    escalation_after: asDuration(value),
                  },
                })
              }
            />
            <Typography.Text> ngày</Typography.Text>
          </label>
        </Flex>
        <Flex justify="end" gap="small" align="center" wrap>
          {lock ? (
            <Tooltip title={lock}>
              <span>{saveButton}</span>
            </Tooltip>
          ) : (
            saveButton
          )}
        </Flex>
      </Flex>
    </Card>
  );
}

function PackagingCard({ canWrite }: { canWrite: boolean }) {
  const { message } = App.useApp();
  const online = useOnline();
  const resource = useCachedResource(
    "supply-chain:packaging-policy",
    useCallback(() => apiClient().getPackagingPolicy(), []),
  );
  const [saving, setSaving] = useState(false);
  const lock = !canWrite ? PACKAGING_LOCK : !online ? OFFLINE_LOCK : null;
  const policy: PackagingPolicy | null = resource.data ?? null;

  const change = async (required: boolean) => {
    if (!policy) return;
    setSaving(true);
    try {
      await apiClient().setPackagingPolicy({
        ...policy,
        require_pre_production_test: required,
      });
      message.success("Đã lưu quy tắc test tiền sản xuất.");
      resource.reload();
    } catch (error) {
      message.error(errorMessage(error));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="Test tiền sản xuất (bước 12–13)"
      extra={policy ? <Tag>policy {policy.policy_version}</Tag> : null}
    >
      {!policy ? (
        resource.error != null ? (
          <LoadError compact error={resource.error} onRetry={resource.reload} />
        ) : (
          <RegionState kind="loading" compact />
        )
      ) : (
        <Flex vertical gap="small">
          <Flex gap="small" align="center" wrap>
            <Tooltip title={lock ?? undefined}>
              <Switch
                aria-label="Bắt buộc test tiền sản xuất đạt trước khi vào sản xuất"
                checked={policy.require_pre_production_test}
                loading={saving}
                disabled={lock !== null}
                onChange={(checked) => void change(checked)}
              />
            </Tooltip>
            <Typography.Text>
              Hồ sơ PO chỉ vào sản xuất khi test tiền sản xuất đã đạt
            </Typography.Text>
          </Flex>
          {lock && <Typography.Text>{lock}</Typography.Text>}
        </Flex>
      )}
    </Card>
  );
}
