"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Flex, Input, Tag, Typography } from "antd";
import { SplitCellsOutlined } from "@ant-design/icons";
import type { AdminSodRule } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../../lib/session";
import { useAuth } from "../../../lib/auth/auth-context";
import { formatDateTime } from "../../../lib/dates";
import { errorMessage as errorText } from "../../../lib/error-message";

export default function SeparationOfDutiesPage() {
  const { hasScope } = useAuth();

  if (!hasScope("platform.roles.read")) {
    return (
      <RegionState
        kind="forbidden"
        description="Cần quyền xem danh mục vai để xem trang này."
      />
    );
  }
  return <RuleList canDecide={hasScope("platform.sod_waivers.write")} />;
}

function RuleList({ canDecide }: { canDecide: boolean }) {
  const [rules, setRules] = useState<AdminSodRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiClient()
      .listSeparationOfDuties()
      .then(setRules)
      .catch((e: unknown) => setError(errorText(e)));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        icon={<SplitCellsOutlined />}
        title="Tách nhiệm"
        subtitle="Những cặp nhiệm vụ không một người nào được giữ cùng lúc. Công ty quá nhỏ để chia hai bên có thể miễn một luật cho phép miễn, kèm lý do; một quản trị viên thứ hai phải xác nhận thì miễn trừ mới có hiệu lực. Mọi lần miễn, xác nhận và thu hồi đều ghi vào nhật ký kiểm toán."
      />
      <Flex vertical gap="middle">
        {error && <Alert type="error" showIcon title={error} />}
        {rules === null ? (
          <RegionState kind="loading" />
        ) : rules.length === 0 ? (
          <RegionState
            kind="empty"
            title="Chưa có luật nào"
            description="Chưa cài luật tách nhiệm nào."
          />
        ) : (
          rules.map((rule) => (
            <RuleCard
              key={rule.key}
              rule={rule}
              canDecide={canDecide}
              onDecided={() => {
                setError(null);
                load();
              }}
            />
          ))
        )}
      </Flex>
    </div>
  );
}

function RuleCard({
  rule,
  canDecide,
  onDecided,
}: {
  rule: AdminSodRule;
  canDecide: boolean;
  onDecided: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const waived = rule.waiver !== null;
  // Proposed by one admin, not yet confirmed by another: it lifts nothing.
  const pending = rule.waiver !== null && rule.waiver.confirmed_at === null;

  const decide = async (action: "waive" | "confirm" | "revoke") => {
    if (!reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "revoke") {
        await apiClient().revokeSeparationOfDutiesWaiver(rule.key, reason);
      } else if (action === "confirm") {
        await apiClient().confirmSeparationOfDutiesWaiver(rule.key, reason);
      } else {
        await apiClient().waiveSeparationOfDutiesRule(rule.key, reason);
      }
      setReason("");
      onDecided();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const reasonId = `sod-reason-${rule.key}`;
  return (
    <Card
      title={rule.description}
      extra={
        pending ? (
          <Tag color="processing">Chờ xác nhận</Tag>
        ) : waived ? (
          <Tag color="warning">Đang miễn</Tag>
        ) : rule.waivable ? (
          <Tag>Đang áp dụng</Tag>
        ) : (
          <Tag bordered={false}>Luôn áp dụng</Tag>
        )
      }
    >
      <Flex vertical gap="middle">
        <Typography.Text code className="text-xs">
          {rule.key}
        </Typography.Text>
        <div className="grid gap-2 sm:grid-cols-2">
          <ScopeList title="Một bên" scopes={rule.left_scopes} />
          <ScopeList title="Bên kia" scopes={rule.right_scopes} />
        </div>

        {rule.waiver && (
          <Alert
            type={pending ? "info" : "warning"}
            title={
              <>
                {pending ? "Đề xuất" : "Miễn"} lúc{" "}
                {formatDateTime(rule.waiver.granted_at)}: {rule.waiver.reason}
                {pending &&
                  " Một quản trị viên khác phải xác nhận; tới lúc đó luật vẫn áp dụng."}
              </>
            }
          />
        )}

        {!rule.waivable && (
          <Typography.Text type="secondary">
            Nền tảng không cho công ty nào miễn luật này.
          </Typography.Text>
        )}

        {canDecide && (rule.waivable || waived) && (
          <Flex vertical gap="small">
            <label htmlFor={reasonId}>
              <Typography.Text strong>Lý do</Typography.Text>
            </label>
            <Input.TextArea
              id={reasonId}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={
                pending
                  ? "Vì sao bạn xác nhận (hoặc rút) miễn trừ này"
                  : waived
                    ? "Vì sao không cần miễn trừ nữa"
                    : "Vì sao công ty không tách được hai nhiệm vụ này"
              }
              rows={2}
            />
            {error && <Alert type="error" showIcon title={error} />}
            <Flex wrap gap="small">
              {pending && (
                <Button
                  danger
                  type="primary"
                  loading={busy}
                  disabled={!reason.trim()}
                  onClick={() => void decide("confirm")}
                >
                  Xác nhận miễn trừ
                </Button>
              )}
              <Button
                danger={!waived}
                loading={busy && !pending}
                disabled={busy || !reason.trim()}
                onClick={() => void decide(waived ? "revoke" : "waive")}
              >
                {pending
                  ? "Rút đề xuất"
                  : waived
                    ? "Thu hồi miễn trừ"
                    : "Đề xuất miễn trừ"}
              </Button>
            </Flex>
          </Flex>
        )}
      </Flex>
    </Card>
  );
}

function ScopeList({ title, scopes }: { title: string; scopes: string[] }) {
  return (
    <Flex vertical gap={4}>
      <Typography.Text type="secondary" className="text-xs">
        {title}
      </Typography.Text>
      <Flex wrap gap={4}>
        {scopes.map((scope) => (
          <Tag key={scope} className="font-mono">
            {scope}
          </Tag>
        ))}
      </Flex>
    </Flex>
  );
}
