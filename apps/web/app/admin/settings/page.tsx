"use client";

import { useEffect, useState } from "react";
import {
  Alert,
  App,
  Button,
  Card,
  Flex,
  Input,
  Select,
  Tag,
  Typography,
} from "antd";
import { SaveOutlined, SettingOutlined } from "@ant-design/icons";
import type { AdminTenant, AutonomyLevel } from "@dw/contracts";
import { PageHeader, RegionState } from "@dw/ui";
import { apiClient } from "../../../lib/session";
import { useAuth } from "../../../lib/auth/auth-context";
import { errorMessage as errorText } from "../../../lib/error-message";
import { tenantStatus } from "../../../lib/tenant-status";

export default function SettingsPage() {
  const { hasScope } = useAuth();

  if (!hasScope("platform.tenant.settings.write")) {
    return (
      <RegionState
        kind="forbidden"
        description="Cần quyền sửa thiết lập công ty để xem trang này."
      />
    );
  }
  return <TenantSettingsForm />;
}

type RecordVisibility = "open" | "restricted";

// What each ceiling lets this tenant's workers do without asking a person.
// A ceiling only ever lowers a worker's own level. A critical action, or a tool
// whose author requires approval, always asks — no ceiling changes that.
const AUTONOMY_OPTIONS: ReadonlyArray<{ level: AutonomyLevel; label: string }> =
  [
    {
      level: "A0",
      label: "A0 — Chạy bóng: đề xuất mọi thứ, không tự làm gì",
    },
    { level: "A1", label: "A1 — Tự đọc; hỏi trước mọi thay đổi" },
    {
      level: "A2",
      label: "A2 — Tự sửa dữ liệu trong hệ thống; hỏi trước khi ra ngoài",
    },
    {
      level: "A3",
      label: "A3 — Ra ngoài được, khi thao tác chạy lại an toàn",
    },
    {
      level: "A4",
      label: "A4 — Không giới hạn theo công ty: worker chạy ở mức nó được dựng",
    },
  ];

function TenantSettingsForm() {
  const [tenant, setTenant] = useState<AdminTenant | null>(null);
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [locale, setLocale] = useState("");
  const [recordVisibility, setRecordVisibility] =
    useState<RecordVisibility>("open");
  const [autonomy, setAutonomy] = useState<AutonomyLevel>("A0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { message } = App.useApp();

  const apply = (t: AdminTenant) => {
    setTenant(t);
    setName(t.name);
    setTimezone(t.timezone ?? "");
    setLocale(t.locale ?? "");
    setRecordVisibility(
      t.record_visibility === "restricted" ? "restricted" : "open",
    );
    setAutonomy(t.max_autonomy_level);
  };

  useEffect(() => {
    apiClient()
      .getTenantSettings()
      .then(apply)
      .catch((e: unknown) => setError(errorText(e)));
  }, []);

  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await apiClient().updateTenantSettings({
        name: name.trim(),
        timezone: timezone.trim(),
        locale: locale.trim(),
        record_visibility: recordVisibility,
        max_autonomy_level: autonomy,
      });
      apply(updated);
      message.success("Đã lưu thiết lập.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        icon={<SettingOutlined />}
        title="Thiết lập công ty"
        subtitle="Tên công ty, múi giờ, ngôn ngữ và giới hạn tự chủ của worker."
      />
      <Flex vertical gap="middle">
        {error && <Alert type="error" showIcon title={error} />}
        {tenant === null ? (
          error ? null : (
            <RegionState kind="loading" />
          )
        ) : (
          <Card
            title={tenant.slug}
            extra={
              <Tag color={tenantStatus(tenant.status).color}>
                {tenantStatus(tenant.status).label}
              </Tag>
            }
          >
            <Flex vertical gap="middle">
              <Typography.Text type="secondary">
                Mã và trạng thái cố định; chỉ đổi được tên, múi giờ, ngôn ngữ và
                các giới hạn dưới đây.
              </Typography.Text>
              <Field id="tenant-name" label="Tên">
                <Input
                  id="tenant-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              <Field id="tenant-timezone" label="Múi giờ">
                <Input
                  id="tenant-timezone"
                  value={timezone}
                  onChange={(e) => setTimezone(e.target.value)}
                  placeholder="Asia/Ho_Chi_Minh"
                />
              </Field>
              <Field id="tenant-locale" label="Ngôn ngữ">
                <Input
                  id="tenant-locale"
                  value={locale}
                  onChange={(e) => setLocale(e.target.value)}
                  placeholder="vi-VN"
                />
              </Field>
              <Field
                id="tenant-visibility"
                label="Phạm vi xem bản ghi"
                help={
                  recordVisibility === "restricted"
                    ? "Bảng số liệu theo tuyến báo cáo: mỗi quản lý chỉ thấy số của mình và người báo cáo cho mình (lãnh đạo vẫn thấy hết)."
                    : "Mọi thành viên của workspace thấy hết dữ liệu, không giới hạn theo tuyến báo cáo."
                }
              >
                <Select
                  id="tenant-visibility"
                  value={recordVisibility}
                  onChange={setRecordVisibility}
                  options={[
                    {
                      value: "open",
                      label: "Mở — ai trong workspace cũng thấy hết",
                    },
                    {
                      value: "restricted",
                      label: "Hạn chế — quản lý chỉ thấy nhóm của mình",
                    },
                  ]}
                />
              </Field>
              <Field
                id="tenant-autonomy"
                label="Giới hạn tự chủ của worker"
                help="Mức cao nhất mà worker của công ty được làm mà không hỏi. Nó chỉ giữ worker dưới mức được dựng, không bao giờ nâng lên. Thao tác nghiêm trọng luôn chờ người quyết."
              >
                <Select
                  id="tenant-autonomy"
                  value={autonomy}
                  onChange={setAutonomy}
                  options={AUTONOMY_OPTIONS.map((option) => ({
                    value: option.level,
                    label: option.label,
                  }))}
                />
              </Field>
              <div>
                <Button
                  type="primary"
                  icon={<SaveOutlined aria-hidden />}
                  loading={busy}
                  disabled={!name.trim()}
                  onClick={() => void save()}
                >
                  Lưu
                </Button>
              </div>
            </Flex>
          </Card>
        )}
      </Flex>
    </div>
  );
}

function Field({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <Flex vertical gap={4}>
      <label htmlFor={id}>
        <Typography.Text strong>{label}</Typography.Text>
      </label>
      {children}
      {help && (
        <Typography.Text type="secondary" className="text-xs">
          {help}
        </Typography.Text>
      )}
    </Flex>
  );
}
